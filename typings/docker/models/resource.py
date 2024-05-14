class Model:
    name: str

    class ExecRes:
        output: bytes
        exit_code: int

    def exec_run(self, cmd: str, demux: bool, privileged: bool) -> ExecRes:
        ...

    def kill(self) -> None:
        ...

    def stats(self, stream: bool = True, decode: bool = False) -> dict:
        ...
