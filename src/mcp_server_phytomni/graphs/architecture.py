# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Static non-secret architecture metadata for generated graph manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..public_agent_catalog import PUBLIC_AGENT_CATALOG, Driver, Topology


@dataclass(frozen=True, slots=True)
class GraphArchitecture:
    """Non-secret graph ownership and dependency projection."""

    classification: Literal["public", "internal"]
    public_agent: str | None
    lifecycle: tuple[str, ...]
    subgraph_dependencies: tuple[str, ...] = ()
    remote_providers: tuple[str, ...] = ()
    driver: Driver | None = None
    topology: Topology | None = None


GRAPH_ARCHITECTURE: dict[str, GraphArchitecture] = {
    item.graph_id: GraphArchitecture(
        classification="public",
        public_agent=item.tool,
        lifecycle=item.modes,
        subgraph_dependencies=item.subgraph_dependencies,
        remote_providers=item.remote_providers,
        driver=item.driver,
        topology=item.topology,
    )
    for item in PUBLIC_AGENT_CATALOG
    if item.graph_id is not None
}
GRAPH_ARCHITECTURE.update(
    {
        "environment": GraphArchitecture(
            "internal",
            None,
            ("asynchronous",),
            remote_providers=("vci_task_platform",),
            driver="remote_task",
            topology="serial",
        ),
        "evolution": GraphArchitecture(
            "internal",
            None,
            ("asynchronous",),
            remote_providers=("analysis_task_platform",),
            driver="remote_task",
            topology="serial",
        ),
    }
)
