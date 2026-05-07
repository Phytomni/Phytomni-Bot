#!/usr/bin/env python3
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Normalize species_data_list.json: sort keys and format with indent."""

import json
from pathlib import Path

CONFIG_FILE = (
    Path(__file__).parent.parent
    / "src"
    / "mcp_server_phytomni"
    / "config"
    / "species_data_list.json"
)


def main() -> None:
    with open(CONFIG_FILE, "r") as f:
        species_data_list = json.load(f)

    with open(CONFIG_FILE, "w") as f:
        json.dump(
            species_data_list, f, ensure_ascii=False, indent=4, sort_keys=True
        )


if __name__ == "__main__":
    main()
