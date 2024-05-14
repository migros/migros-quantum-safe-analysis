from .models.containers import ContainerCollection

class DockerClient:
    containers: ContainerCollection
    def from_env() -> "DockerClient": ...  # type:ignore

from_env = DockerClient.from_env
