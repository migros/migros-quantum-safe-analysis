"""Module handling data collection from containers. From setup to starting and stopping requests."""
import datetime
import random
import string
import threading
import time
from typing import Any, Generator

import more_itertools
import requests
from docker.client import DockerClient
from docker.errors import APIError
from docker.models.resource import Model

TIMEOUT_SEC = 9


class ClientPerfCollector:
    """Collects Latency Data by creating load on client endpoint and observing response timings"""

    url: str
    collector_thread: threading.Thread
    stop_event: threading.Event
    data: list[dict[str, Any]]
    message_length: int

    def __init__(self, client_url, msg_length: int = 5):
        self.url = client_url
        self.collector_thread = threading.Thread(target=self.load_continuous, daemon=True)
        self.data = []
        self.message_length = msg_length
        self.stop_event = threading.Event()

    def start_collecting(self):
        """Starts collecting data"""
        self.collector_thread.start()

    def stop_collecting(self):
        """Seizes collecting data and returns all collected data points."""
        if self.stop_event.is_set():
            raise RuntimeError("Collector was already stopped before")
        self.stop_event.set()
        self.collector_thread.join()
        print("  Client Load ENDED")
        return self.data

    def load_continuous(self):
        """Started in a separate thread. Responsible for issuing requests and observing responses."""
        print("  Client Load STARTED")
        data = []
        request_id = 0
        while not self.stop_event.is_set():
            # Make batch_size requests in parallel and measure timings
            msg = random.choices(string.ascii_lowercase, k=self.message_length)
            msg = "".join(msg)
            start = time.time_ns() / 1_000_000_000
            elapsed = -1
            try:
                r = requests.post(url=self.url, data={"message": msg}, timeout=TIMEOUT_SEC)
                elapsed = r.elapsed.total_seconds()
            except requests.ReadTimeout:
                elapsed = TIMEOUT_SEC
            data.append(
                {
                    "id": request_id,
                    "msg_length": self.message_length,
                    "latency": elapsed,
                    "start": start,  # start time of request in epoch s
                }
            )
            request_id += 1
        self.data = data


class DockerStatCollector:
    """Collects performance statistics from Docker containers by calling `docker stats`"""

    containers: list[Model] | None
    num_containers: int
    collector_thread: threading.Thread
    stop_event: threading.Event
    data: list[dict[str, Any]]
    client: DockerClient

    def __init__(self, client: DockerClient):
        self.containers = None
        self.num_containers = 0
        self.collector_thread = threading.Thread(target=self.collect, daemon=True)
        self.data = []
        self.client = client
        self.stop_event = threading.Event()

    def start_collecting(self):
        """Starts collecting data"""
        self.collector_thread.start()

    def stop_collecting(self):
        """Seizes collecting data and returns all collected data points."""
        if self.stop_event.is_set():
            raise RuntimeError("Collector was already stopped before")
        self.stop_event.set()
        self.collector_thread.join()
        print("  Docker Stats ENDED")
        return self.data

    def set_containers(self, containers):
        """Sets containers to observe. `containers` should be the output of `client.containers.list()`."""
        self.containers = containers
        self.num_containers = len(containers)

    def stream_generator(self) -> Generator[dict[str, Any], None, None]:
        """Generator for docker stats data. Interleaves `docker stats` streams from all containers."""
        while self.containers is None:
            # call API to get list of containers
            # extract one data point each, repeat
            try:
                tmp_conts = self.client.containers.list()
                for x in tmp_conts:
                    yield x.stats(stream=False)
            except APIError:  # Thrown if no containers exist
                continue

        streams = [cont.stats(decode=True, stream=True) for cont in self.containers]
        for x in more_itertools.roundrobin(*streams):
            yield x

    def collect(self):
        """Collect data from all containers if self.containers is None.
        Otherwise use containers specified in self.set_containers() and avoid an API call"""
        print("  Docker Stats STARTED")
        data = []
        while not self.stop_event.is_set():
            for data_point in self.stream_generator():
                # X iters process all current data points from X containers
                point = extract(data_point)
                if point:
                    data.append(point)
                if self.stop_event.is_set():
                    break
        # same amount of data points per container
        self.data = data[: -(len(data) % self.num_containers)]


def extract(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract relevant data from docker stats response (usage since last measurement)"""
    # Timestamp: "read"
    timestamp = data["read"]
    # Network: "networks/*/rx_bytes" + "networks/*/tx_bytes" (sum over all if)
    total_net_traffic = 0
    if "networks" not in data.keys():
        return None  # Incomplete data record (e.g. for openssl-gen)
    for i_face in data["networks"].keys():
        total_net_traffic += data["networks"][i_face]["rx_bytes"]
        total_net_traffic += data["networks"][i_face]["tx_bytes"]

    # Memory usage: "memory_stats/usage", "memory_stats/limit"
    memory_usage = data["memory_stats"]["usage"] / data["memory_stats"]["limit"]

    # CPU usage: "cpu_stats/cpu_usage/total_usage" - "precpu_stats/cpu_usage/total_usage" divided by
    # "cpu_stats/cpu_usage/system_cpu_usage" - "precpu_stats/cpu_usage/system_cpu_usage"
    try:
        cont_diff = data["cpu_stats"]["cpu_usage"]["total_usage"] - data["precpu_stats"]["cpu_usage"]["total_usage"]
        sys_diff = data["cpu_stats"]["system_cpu_usage"] - data["precpu_stats"]["system_cpu_usage"]
        cpu_usage = cont_diff / sys_diff
    except KeyError:
        cpu_usage = 0

    timestamp_parsed = datetime.datetime.fromisoformat(timestamp).timestamp()

    return {
        "time": timestamp_parsed,
        "container": data["name"][1:],
        "total_net_traffic": total_net_traffic,
        "memory_usage": memory_usage,
        "cpu_usage": cpu_usage,
    }
