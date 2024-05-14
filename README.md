# mgb-quantum-safe-analysis

## Description

This repository is used to evaluate the performance of different versions of [The Migros Quantum-Safe Prototype](https://github.com/migros/migros-quantum-safe).
It simulates different network conditions in the Docker deployment and analyzes different aspects of the prototype system.

## Results

Please see the analysis results in the `data-*` folder(s) for the observed results.
The folder name indicates the testing conditions as `data-[bandwidth]-[delay]-[loss rate]-[test duration]`.
The limits apply per interface and are directly used in a `tc qdisc add dev {iface} root netem ...` command.

## Installation

First, you need to have a setup capable of running the prototype's build.
Check out the prototype's repository above for detailed requirements.

To run these analyses yourself, install (at least) the following python packages with `pip install -r requirements.txt`:

* GitPython
* docker-py
* more-itertools
* matplotlib

## Usage

To execute the analysis, use the `analysis.py` file or the prepared VSCode Tasks.

## Support

Please contact [marc.himmelberger@mgb.ch](mailto:marc.himmelberger@mgb.ch) for issues relating to this repository. You are also welcome to open Issues and Pull Requests directly.  
For general questions about quantum-safety at Migros, please use [security-architecture@mgb.ch](mailto:security-architecture@mgb.ch).  
For press inquiries, please  contact [media@migros.ch](mailto:media@migros.ch).

## License

This project is provided under the [Apache License 2.0](LICENSE.txt).  
We encourage feedback and open discussion on this GitHub page.
