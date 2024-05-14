#!/usr/bin/env python3
"""Module providing main analysis workflow. Reading/writing results and starting plotting."""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
from threading import Thread

import docker
import git

from data_collection import ClientPerfCollector, DockerStatCollector
from render_charts import render_folder


def run_cmd_background(cmd: list[str], cwd, expected=None, timeout=20) -> subprocess.Popen:
    """Runs the given shell command in the background. Possibly checks for an expected string in stdout or stderr upon
    which the method returns but the command keeps running in a subprocess."""
    cmd_str = " ".join(cmd)
    # Did not use retry mechanism here because docker compose is less error-prone
    print(f"  ... {cmd_str}", end="\r")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    if expected:
        # Wait for expected output within specified timeout
        buffer = b""
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                raise RuntimeError(f"Error: command '{cmd_str}' did not produce expected output in time\n", buffer)
            # Will hang if there is no output at all
            line = proc.stdout.readline()  # type:ignore
            buffer = buffer + line
            if expected in line:
                print(f"  {cmd_str}: {'HEALTHY -> bg'.ljust(60)}")
                break
            else:
                print(f"  ... {cmd_str}: {line.decode().strip()[:60].ljust(60)}", end="\r")
    return proc


def run_cmd(cmd: list[str], cwd, expected=None, timeout=20):
    """Runs the given shell command in the foreground. Possibly checks for an expected string in stdout or stderr."""
    cmd_str = " ".join(cmd)
    while True:
        print(f"  ... {cmd_str}", end="\r")
        try:
            out = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                timeout=timeout,
            )
            print(f"  {cmd_str}: DONE")
            if expected:
                if expected not in out:
                    raise RuntimeError(f"Error: command '{cmd_str}' failed\n", out)
            return
        except subprocess.TimeoutExpired:
            print(f" timed out ({timeout}s), retrying")


def run_analysis(
    repository: git.Repo,
    branch_name: str,
    max_bw: str,
    min_lat: str,
    loss_perc: str,
    total_time: int,
    spinup_time: int,
    msg_length: int,
):
    """Runs one analysis for the given branch. Branch must be valid."""
    print(f"---- Analyzing branch '{branch_name}' ----")

    # Remove possible leftover containers
    run_cmd(["docker", "compose", "down", "-t", "1"], repository.working_tree_dir)

    # Reset repo to correct branch head
    repository.git.checkout(branch_name)
    # repo.head.reset(branch, working_tree=True)

    # Run build tasks to generate artifacts
    run_cmd(["mvn", "-B", "test"], repository.working_tree_dir, b"BUILD SUCCESS")
    run_cmd(["mvn", "-B", "package"], repository.working_tree_dir, b"BUILD SUCCESS")
    print("  maven BUILD SUCCESS")

    # Init docker API
    client = docker.from_env()

    # Definitely stop other running containers
    for cont in client.containers.list():
        cont.kill()

    # also monitor network traffic during startup (e.g. IPsec)
    stat_collector = DockerStatCollector(client)
    stat_collector.start_collecting()

    # Start containers: detached start
    docker_compose_proc = run_cmd_background(
        ["docker", "compose", "up", "--build", "--force-recreate"],
        repository.working_tree_dir,
        b"I am ready to interact with the system at jwt-client:80/interact/",
        timeout=360,
    )

    containers = client.containers.list()
    stat_collector.set_containers(containers)

    # Enable network restrictions on all interfaces using tc directly on container
    iface_regex = r"\d+: (eth\d)"
    tc_delay = f"delay {min_lat}"  # restrict latency, uses small variation
    tc_rate = f"rate {max_bw}"  # restrict bandwidth
    tc_loss = f"loss {loss_perc}"  # cause loss
    for cont in containers:
        # Find all ethX interfaces
        ip_link_show = cont.exec_run("ip link show", demux=False, privileged=True).output.decode()
        for iface in re.findall(iface_regex, ip_link_show):
            tc_cmd = f"tc qdisc add dev {iface} root netem {tc_delay} {tc_rate} {tc_loss}"
            out = cont.exec_run(tc_cmd, demux=False, privileged=True)
            if out.exit_code == 0:
                print(f"  tc command on {cont.name}/{iface}: DONE".ljust(70), end="\r")
            else:
                print(f"  tc command on {cont.name}/{iface}: ERROR ({out.exit_code})".ljust(70))
                raise RuntimeError(
                    f"Error: command '{tc_cmd}' did not produce expected output on {cont.name}\n", out.output
                )
    print("  tc commands: DONE".ljust(70))

    # Measure net I/O (bandwidth), CPU, RAM w/ docker stats
    # Gather streams (produce ca one data point per second)
    load_collector = ClientPerfCollector("http://localhost:8080/run-interaction/", msg_length=msg_length)

    # Access client
    load_collector.start_collecting()

    print(f"  ... querying for {total_time}s  ", end="\r")
    time.sleep(total_time)
    print("  ... wrapping up                  ")

    client_perf_data = load_collector.stop_collecting()
    docker_stats_data = stat_collector.stop_collecting()

    # Remove data before spin-up time to measure only steady-state
    min_time_perf = min(map(lambda x: x["start"], client_perf_data))
    min_time_stats = min(map(lambda x: x["time"], docker_stats_data))
    min_time_measurement = min(min_time_perf, min_time_stats)
    client_perf_data = list(filter(lambda x: x["start"] > spinup_time + min_time_measurement, client_perf_data))
    docker_stats_data = list(filter(lambda x: x["time"] > spinup_time + min_time_measurement, docker_stats_data))

    # Remove leftover containers
    docker_compose_proc.kill()
    docker_compose_proc.wait()
    for cont in containers:
        t = Thread(target=cont.kill, daemon=True)
        t.start()
        t.join(3)
        if t.is_alive():
            # Timed out, container is probably stuck
            print(f"  [ERR] unable to kill {cont.name}")

    print(f"  Collected {len(docker_stats_data)} data points from docker stats.")
    print(f"  Collected {len(client_perf_data)} data points from client performance tests.")
    print()

    return {"docker_stats": docker_stats_data, "client_perf": client_perf_data}


if __name__ == "__main__":
    REPO_URL = "https://github.com/migros/migros-quantum-safe.git"

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description=f"Analyze system performance at multiple points in time from the repository '{REPO_URL}',"
        + "as identified by branch heads. The results are rendered to images and at 'results/'.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "branches",
        type=str,
        nargs="*",
        help="a branch in the target repository to analyze. Defaults to the set of all branches.",
    )

    parser.add_argument(
        "--max-bandwidth",
        type=str,
        default="500mbit",
        help="maximum bandwidth at which the network is capped.",
    )
    parser.add_argument(
        "--min-latency",
        type=str,
        default="10ms",
        help="minimum latency imposed on each network interface (twice per link).",
    )
    parser.add_argument(
        "--percent-loss",
        type=str,
        default="0.1%",
        help="chance of dropping any given packet.",
    )
    parser.add_argument(
        "--spinup",
        type=int,
        default="15",
        help="time in s to reserve for spinning up containers. Will be discarded from analysis.",
    )
    parser.add_argument(
        "--time",
        type=int,
        default="30",
        help="total time in s to run analysis for.",
    )
    parser.add_argument(
        "--message-size",
        type=int,
        default="500",
        help="size of message to be sent to JWT-Creator in bytes.",
    )
    parser.add_argument(
        "--skip-all-analyze",
        action="store_true",
        help="skips analysis completely and only uses data folders to render charts.",
    )
    parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="skips analysis for specific branches if data file is already present.",
    )

    args = parser.parse_args()

    data_dir = os.path.join(
        os.path.dirname(__file__),
        f"data-{args.max_bandwidth}-{args.min_latency}-{args.percent_loss}-{args.spinup}s-{args.time}s",
    )

    if not args.skip_all_analyze:
        # Clone repository
        repo: git.Repo
        work_dir = os.path.join(os.path.dirname(__file__), "git_clone")
        try:
            if os.path.exists(work_dir):
                shutil.rmtree(work_dir)
            print("Cloning repo...", end="\r")
            repo = git.Repo.clone_from(REPO_URL, work_dir)
            print("Cloning repo: DONE")
        except Exception as e:
            raise RuntimeError(f"Error cloning repository {REPO_URL}") from e

        avail_branches = repo.remote().refs
        avail_branches = [x.name.split("/")[-1] for x in avail_branches]
        avail_branches.remove("HEAD")
        if args.branches:
            # Validate branches
            for branch in args.branches:
                if branch not in avail_branches:
                    print(f"Error: Provided branch '{branch}' not found in actual branches: {avail_branches}")
                    quit(-1)
            branches = args.branches
        else:
            # Default: all branches in repo
            branches = avail_branches

        if not os.path.exists(data_dir):
            os.mkdir(data_dir)

        for branch in branches:
            out_file = os.path.join(data_dir, f"{branch}.json")
            if args.skip_analyze and os.path.exists(out_file):
                print(f"Analysis skipped for branch '{branch}'")
            else:
                res = run_analysis(
                    repo,
                    branch,
                    args.max_bandwidth,
                    args.min_latency,
                    args.percent_loss,
                    args.time,
                    args.spinup,
                    args.message_size,
                )

                # Overwrite existing files
                if os.path.exists(out_file):
                    os.remove(out_file)
                with open(out_file, "w", encoding="UTF-8") as f:
                    json.dump(res, f)

    # Render charts from data
    render_folder(data_dir)
