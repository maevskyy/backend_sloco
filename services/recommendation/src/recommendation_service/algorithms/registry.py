from dataclasses import dataclass


@dataclass(frozen=True)
class AlgorithmDescriptor:
    name: str
    version: str


class AlgorithmRegistry:
    def __init__(self) -> None:
        self._algorithms: dict[str, AlgorithmDescriptor] = {}

    def register(self, descriptor: AlgorithmDescriptor) -> None:
        self._algorithms[descriptor.name] = descriptor

    def list_algorithms(self) -> list[AlgorithmDescriptor]:
        return sorted(self._algorithms.values(), key=lambda item: item.name)


registry = AlgorithmRegistry()
