"""Module providing plotting functionality for captured data."""
import itertools
import json
import os
from glob import glob
from statistics import fmean
from typing import Any

import numpy
from matplotlib import pyplot
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter


def render_time_chart(
    plot: pyplot.Axes,
    timestamps: list[list[Any]],
    title: str | None,
    y_axis_label: str,
    series: list[list[Any]],
    legend: list[str],
    is_last: bool,
    y_max: float | None = None,
):
    """Creates line chart showing some qunatity changing over the lifetime of the experiment."""
    assert len(legend) == len(series)
    for ser, times, leg in zip(series, timestamps, legend):
        assert len(ser) == len(times)

        # Ignore data where entry is ''
        indices = []
        for i, s in enumerate(ser):
            if not s == "":
                indices.append(i)
        x = [times[i] for i in indices]
        y = [ser[i] for i in indices]
        plot.plot(x, y, label=leg if is_last else "")
        if y_max:
            plot.set_ylim(0, y_max)
    if title:
        plot.set_title(title)
    plot.set_ylabel(y_axis_label)
    if is_last:
        plot.set_xlabel("Time from startup [seconds]")
    if "percent" in y_axis_label:
        plot.yaxis.set_major_formatter(PercentFormatter(1))


def render_histogram(plot: pyplot.Axes, title: str, y_axis_label: str, data: list[Any]):
    """Creates histogram showing the distribution of some qunatity over the lifetime of the experiment."""
    xmin = 0
    xmax = 0.6
    bins = numpy.linspace(xmin, xmax, 100)
    plot.hist(numpy.clip(data, bins[0], bins[-1]), bins)
    plot.set_title(title)
    plot.set_xlabel(y_axis_label)
    plot.set_xlim(xmin, xmax)


def parse_branch(folder, branch):
    """Opens saved data for a specific experiment run. Returns appropriate objects for each quantity."""
    print(f"Creating charts for {os.path.basename(folder)}/{branch}")

    with open(os.path.join(folder, f"{branch}.json"), "r", encoding="UTF-8") as f:
        data = json.load(f)
        docker_stats_data = data["docker_stats"]
        client_perf_data = data["client_perf"]

    # Histogram Data
    latencies = list(map(lambda x: x["latency"], client_perf_data))

    # Charts from docker stats (time-based)
    containers = ["jwt-client", "jwt-creator", "jwt-verifier", "cert-auth", "swan-carol", "swan-moon"]
    cpu_usage: list[list[float]] = []
    ram_usage: list[list[float]] = []
    net_usage: list[list[float]] = []
    timestamps: list[list[float]] = []

    for cont in containers:
        cont_data = list(filter(lambda x: x["container"] == cont, docker_stats_data))  # pylint: disable=W0640
        cont_times = list(map(lambda x: x["time"], cont_data))
        timestamps.append(cont_times)
        cpu_usage.append(list(map(lambda x: x["cpu_usage"], cont_data)))  # CPU chart
        ram_usage.append(list(map(lambda x: x["memory_usage"], cont_data)))  # RAM chart

        # Traffic rate using delta traffic and delta time
        traffic_rate = [0]
        prev_tot_net = cont_data[0]["total_net_traffic"]
        prev_time = cont_times[0]
        for entry, time in zip(cont_data[1:], cont_times[1:]):
            rate = (entry["total_net_traffic"] - prev_tot_net) / (time - prev_time)
            prev_tot_net = entry["total_net_traffic"]
            prev_time = time
            traffic_rate.append(rate)

        # 3 pt. moving average
        window = 3
        average_data: list[float] = [0, 0]
        for ind in range(len(traffic_rate) - window + 1):
            average_data.append(8 / 1000 * float(fmean(traffic_rate[ind : ind + window])))  # also convert to Kbps

        net_usage.append(average_data)  # Network chart

    cpu_max: float = 0
    ram_max: float = 0
    net_max: float = 0
    for l1 in cpu_usage:
        for x in l1:
            if x == "":
                continue
            cpu_max = max(cpu_max, x)
    for l2 in ram_usage:
        for x in l2:
            if x == "":
                continue
            ram_max = max(ram_max, x)
    for l3 in net_usage:
        for x in l3:
            if x == "":
                continue
            net_max = max(net_max, x)

    # Timestamp as offset from start
    min_timestamp = min(itertools.chain(*timestamps))
    timestamps = list(map(lambda x: list(map(lambda y: y - min_timestamp, x)), timestamps))

    return (
        containers,
        latencies,
        timestamps,
        cpu_usage,
        cpu_max,
        ram_usage,
        ram_max,
        net_usage,
        net_max,
    )


def render_branch(
    folder: str,
    branch: str,
    containers: list[str],
    latencies: list[float],
    timestamps: list[list[float]],
    cpu_usage: list[list[float]],
    cpu_max: float,
    ram_usage: list[list[float]],
    ram_max: float,
    net_usage: list[list[float]],
    net_max: float,
):
    """Creates a set of plots for a specific experiment run. Detailing its performance over time."""
    # Layout
    px = 1 / pyplot.rcParams["figure.dpi"]  # pixel in inches
    fig: Figure = pyplot.figure(figsize=(1600 * px, 900 * px), layout="constrained")
    subfigs = fig.subfigures(2, height_ratios=[1, 3])  # type: ignore
    glob_plts: pyplot.Axes = subfigs[0].subplots()
    time_plts = subfigs[1].subplots(3, sharex=True)

    # Latency chart: Not differentiated by containers
    # Histogram with multiple series for message sizes
    render_histogram(
        glob_plts,
        "Request Latencies by Message Size",
        "Latency [seconds]",
        latencies,
    )

    title = "Resource Consumption over Time by Container"
    render_time_chart(time_plts[0], timestamps, title, "CPU Usage [percent]", cpu_usage, containers, False, cpu_max)
    render_time_chart(time_plts[1], timestamps, None, "Memory Usage [percent]", ram_usage, containers, False, ram_max)
    render_time_chart(time_plts[2], timestamps, None, "Network Traffic [Kbps]", net_usage, containers, True, net_max)
    subfigs[1].legend(loc="outside right")

    pyplot.savefig(os.path.join(folder, f"{branch}.png"))


def render_comparison(
    folder: str,
    branches: list[str],
    latency_50: dict[str, float],
    latency_80: dict[str, float],
    latency_90: dict[str, float],
    latency_95: dict[str, float],
    average_cpu: dict[str, float],
    average_ram: dict[str, float],
    average_net: dict[str, float],
):
    """Creates a single diagram comparing multiple different experiment runs in aggregated charts per quantity."""
    # Layout
    px = 1 / pyplot.rcParams["figure.dpi"]  # pixel in inches
    fig: Figure = pyplot.figure(figsize=(1600 * px, 900 * px), layout="constrained")
    subplts: numpy.ndarray = fig.subplots(2, 2)  # type:ignore

    x = numpy.arange(len(branches))
    width = 0.2
    subplts[0, 0].bar(x + 0 * width, [latency_50[b] for b in branches], width, label="50th percentile")
    subplts[0, 0].bar(x + 1 * width, [latency_80[b] for b in branches], width, label="80th percentile")
    subplts[0, 0].bar(x + 2 * width, [latency_90[b] for b in branches], width, label="90th percentile")
    subplts[0, 0].bar(x + 3 * width, [latency_95[b] for b in branches], width, label="95th percentile")
    subplts[0, 0].set_xticks(x + width, branches)
    subplts[0, 0].legend(loc="upper left", ncols=len(branches))
    subplts[0, 0].set_ylim(0, subplts[0, 0].get_ylim()[1] * 1.1)
    subplts[0, 0].set_title("Latency Percentiles")
    subplts[1, 0].set_ylabel("Latency [s]")

    subplts[1, 0].bar(branches, [average_cpu[t] for t in branches])
    subplts[1, 0].set_title("Average CPU Usage")
    subplts[1, 0].set_ylabel("CPU Usage [percent]")
    subplts[1, 0].yaxis.set_major_formatter(PercentFormatter(1))

    subplts[0, 1].bar(branches, [average_ram[t] for t in branches])
    subplts[0, 1].set_title("Average Memory Usage")
    subplts[0, 1].set_ylabel("Memory Usage [percent]")
    subplts[0, 1].yaxis.set_major_formatter(PercentFormatter(1))

    subplts[1, 1].bar(branches, [average_net[t] for t in branches])
    subplts[1, 1].set_title("Average Network Traffic")
    subplts[1, 1].set_ylabel("Network Traffic [Kbps]")

    pyplot.savefig(os.path.join(folder, "comparison.png"))


def render_folder(folder):
    """Renders all diagrams for the data files contained in a given folder."""
    branches = []
    for filename in glob("*.json", root_dir=folder):
        branches.append(filename.split(".json")[0])
    parses = [parse_branch(folder, branch) for branch in branches]

    # Compute branch-global max values
    cpu_max: float = max([x[4] for x in parses])
    ram_max: float = max([x[6] for x in parses])
    net_max: float = max([x[8] for x in parses])

    average_cpu: dict[str, float] = {}
    average_ram: dict[str, float] = {}
    average_net: dict[str, float] = {}
    latency_50: dict[str, float] = {}
    latency_80: dict[str, float] = {}
    latency_90: dict[str, float] = {}
    latency_95: dict[str, float] = {}
    for branch, x in zip(branches, parses):
        (
            containers,
            latencies,
            timestamps,
            cpu_usage,
            _,
            ram_usage,
            _,
            net_usage,
            _,
        ) = x
        render_branch(
            folder,
            branch,
            containers,
            latencies,
            timestamps,
            cpu_usage,
            cpu_max,
            ram_usage,
            ram_max,
            net_usage,
            net_max,
        )

        # Aggregate data for comparison
        latency_50[branch] = float(numpy.percentile(latencies, 50))
        latency_80[branch] = float(numpy.percentile(latencies, 80))
        latency_90[branch] = float(numpy.percentile(latencies, 90))
        latency_95[branch] = float(numpy.percentile(latencies, 95))

        cpu_sum = 0
        ram_sum = 0
        net_sum = 0

        count = 0
        for _, times, cpus, rams, nets in zip(containers, timestamps, cpu_usage, ram_usage, net_usage):
            for _, cpu, ram, net in zip(times, cpus, rams, nets):
                cpu_sum += cpu
                ram_sum += ram
                net_sum += net
                count += 1

        average_cpu[branch] = cpu_sum / count
        average_ram[branch] = ram_sum / count
        average_net[branch] = net_sum / count

    render_comparison(
        folder, branches, latency_50, latency_80, latency_90, latency_95, average_cpu, average_ram, average_net
    )


if __name__ == "__main__":
    # Walk through all data folders & create charts
    for fold in glob("data*"):
        fold_path = os.path.join(os.path.dirname(__file__), fold)
        if os.path.isdir(fold_path):
            render_folder(fold_path)
