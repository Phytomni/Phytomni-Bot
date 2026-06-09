# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Chain the network agent with the deepgenome agent.

Public surface: ChainTop20MissingError, network_to_deep_genome_chain.
``network_to_deep_genome_chain`` runs the existing ``network_analysis``
entry to completion, downloads the analyst's top-20 gene list (a CSV
emitted by the upstream ``construct_network.py`` script and named like
``<TO_ID>_top20_gene.csv``), parses the 20 gene IDs, and submits the
existing ``gene_function`` entry for each gene in parallel via
``asyncio.gather``.

The species name is resolved to a deepgenome ``species_code`` through
``SPECIES_TO_CODE`` (sourced from the description string in
``mcp/schemas.py:178-249``); keep the two in sync if the schema dict
grows.
"""

from __future__ import annotations

import asyncio
import csv
import fnmatch
import logging
from pathlib import Path
from typing import Any, Dict, Final, List, Mapping, Optional

from ...config.defaults import DeepGenomeConfig, GeneNetworkConfig
from ...config.settings import get_sensitive_config
from ...storage.downloads import download_obs_file
from ...storage.obs_relay_ops import list_object_keys
from ...storage.obs_storage import (
    normalize_obs_object_key,
    obsfs_or_sdk,
    obsfs_path_for,
)
from ...storage.path_policy import RunIdentity
from ..deep_genome.agent import gene_function
from .agent import network_analysis

logger = logging.getLogger(__name__)

# Glob pattern matched against files in the network agent's output dir.
# The upstream ``construct_network.py`` script writes a file named
# ``<TO_ID>_top20_gene.csv``; matching the suffix keeps the chain
# robust to TO-id changes.
TOP20_GLOB: Final[str] = "*top20_gene.csv"
TOP20_MAX_ROWS: Final[int] = 20

# Column headers the parser will try, in priority order. Keep this list
# short and conservative — the upstream ``construct_network.py`` script
# is the source of truth; the first matching header wins.
_GENE_ID_HEADER_CANDIDATES: Final[tuple[str, ...]] = (
    "gene_id",
    "GeneID",
    "gene",
    "id",
    "Gene",
)

# Hand-maintained mapping from the network agent's "Latin name in
# lowercase with spaces" form (the ``species`` schema in
# ``mcp/schemas.py:424-443``) to the deepgenome agent's three-letter
# ``species_code`` (the description of ``mcp/schemas.py:178-249``).
# Sync source: ``mcp/schemas.py:178-249``. When the schema description
# gains or removes entries, mirror the change here.
SPECIES_TO_CODE: Final[Mapping[str, str]] = {
    "actinidia chinensis": "ach",
    "ananas comosus": "aco",
    "arabidopsis lyrata": "aly",
    "asparagus officinalis": "aof",
    "aegilops tauschii": "ata",
    "arabidopsis thaliana": "ath",
    "amborella trichopoda": "atr",
    "brachypodium distachyon": "bdi",
    "brassica napus": "bna",
    "brassica oleracea": "bol",
    "brassica rapa": "bra",
    "beta vulgaris": "bvu",
    "capsicum annuum": "can",
    "corylus avellana": "cav",
    "chara braunii": "cbr",
    "coffea canephora": "ccan",
    "citrus clementina": "ccl",
    "citrullus lanatus": "cla",
    "cucumis melo": "cme",
    "chenopodium quinoa": "cqu",
    "chlamydomonas reinhardtii": "cre",
    "cucumis sativus": "csa",
    "daucus carota": "dca",
    "digitaria exilis": "dex",
    "eragrostis curvula": "ecu",
    "eucalyptus grandis": "egr",
    "eutrema salsugineum": "esa",
    "gossypium hirsutum": "ghi",
    "glycine max": "gma",
    "gossypium raimondii": "gra",
    "helianthus annuus": "han",
    "hordeum vulgare": "hvu",
    "lolium perenne": "lpe",
    "lactuca sativa": "lsa",
    "musa acuminata": "mac",
    "manihot esculenta": "mes",
    "marchantia polymorpha": "mpo",
    "medicago truncatula": "mtr",
    "oryza brachyantha": "obr",
    "olea europaea": "oeu",
    "oryza sativa": "osa",
    "panicum hallii": "pha",
    "physcomitrium patens": "ppa",
    "prunus persica": "ppe",
    "pisum sativum": "psa",
    "papaver somniferum": "pso",
    "populus trichocarpa": "ptr",
    "phaseolus vulgaris": "pvu",
    "quercus lobata": "qlo",
    "rosa chinensis": "rch",
    "sorghum bicolor": "sbi",
    "secale cereale": "sce",
    "setaria italica": "sit",
    "solanum lycopersicum": "sly",
    "selaginella moellendorffii": "smo",
    "saccharum spontaneum": "ssp",
    "solanum tuberosum": "stu",
    "setaria viridis": "svi",
    "triticum aestivum": "tae",
    "theobroma cacao": "tca",
    "triticum dicoccoides": "tdi",
    "trifolium pratense": "tpr",
    "triticum turgidum": "ttu",
    "vitis vinifera": "vvi",
    "zea mays": "zma",
}


class ChainTop20MissingError(FileNotFoundError):
    """Raised when the network envelope's output_dir lacks a top20 file.

    Inherits from ``FileNotFoundError`` so callers can handle the
    missing-file case with a single ``except`` block. The network
    ``output_dir`` and the expected filename are interpolated into the
    message so the failure is debuggable from a log line alone.
    """

    def __init__(self, output_dir: str, filename: str, message: str):
        """Initialize the error.

        Args:
            output_dir: Network envelope's output directory.
            filename: Filename that was expected under ``output_dir``.
            message: Human-readable description of the failure.
        """
        self.output_dir = output_dir
        self.filename = filename
        full_message = f"{message} (output_dir={output_dir}, filename={filename})"
        super().__init__(full_message)


def _parse_top20_gene_ids(csv_path: Path) -> List[str]:
    """Parse the top-20 CSV file into a list of gene IDs.

    Uses ``utf-8-sig`` encoding to tolerate a BOM that some upstream
    CSV writers prepend. Picks the first matching column header from
    ``_GENE_ID_HEADER_CANDIDATES``; raises ``ChainTop20MissingError``
    when no candidate header is present. Skips blank rows and caps the
    result at ``TOP20_MAX_ROWS`` entries.

    Args:
        csv_path: Local path to the top-20 gene CSV file.

    Returns:
        List of gene IDs in source order, length up to
        ``TOP20_MAX_ROWS``. May be empty if every row is blank.

    Raises:
        ChainTop20MissingError: When the file has no recognizable
            gene-id column.
    """
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        chosen = next(
            (header for header in _GENE_ID_HEADER_CANDIDATES
             if header in fieldnames),
            None,
        )
        if chosen is None:
            raise ChainTop20MissingError(
                output_dir=str(csv_path.parent),
                filename=csv_path.name,
                message=(
                    "top-20 gene CSV has no recognizable gene-id "
                    f"column; headers={list(fieldnames)}"
                ),
            )
        gene_ids: List[str] = []
        for row in reader:
            if len(gene_ids) >= TOP20_MAX_ROWS:
                break
            value = (row.get(chosen) or "").strip()
            if not value:
                continue
            gene_ids.append(value)
    return gene_ids


def _build_top20_object_key(
    output_dir: str, filename: str, bucket_name: str
) -> str:
    """Build the OBS object key for a top20 file under the network dir.

    Args:
        output_dir: Network envelope's output directory (OBS-style
            absolute path such as ``/obs/<bucket>/<prefix>``).
        filename: Filename to append.
        bucket_name: OBS bucket name used by the network config.

    Returns:
        Relative object key that ``download_obs_file`` can resolve.
    """
    prefix = normalize_obs_object_key(output_dir, bucket_name)
    return f"{prefix.rstrip('/')}/{filename}"


def _obsfs_top20_match(
    output_dir: str, bucket_name: str
) -> Optional[str]:
    """Return the first filename matching ``TOP20_GLOB`` under obsfs.

    Args:
        output_dir: Network envelope's output directory.
        bucket_name: OBS bucket name used by the network config.

    Returns:
        The matched filename (basename only), or ``None`` if obsfs
        is not mounted or the directory is empty.
    """
    try:
        local_dir = obsfs_path_for(output_dir, bucket_name)
    except (OSError, ValueError):
        return None
    if not local_dir.is_dir():
        return None
    matches = sorted(local_dir.glob(TOP20_GLOB))
    if not matches:
        return None
    return matches[0].name


def _sdk_top20_match(
    output_dir: str, bucket_name: str, obs_server: str
) -> Optional[str]:
    """Return the first object key matching ``TOP20_GLOB`` via the SDK.

    Args:
        output_dir: Network envelope's output directory.
        bucket_name: OBS bucket name used by the network config.
        obs_server: OBS endpoint for the SDK client.

    Returns:
        The matched object key (full prefix + filename), or ``None``
        if no file under the prefix matches the glob.
    """
    try:
        keys = list_object_keys(
            bucket=bucket_name,
            prefix=output_dir,
            obs_server=obs_server,
        )
    except OSError:
        return None
    for key in keys:
        basename = key.rsplit("/", 1)[-1]
        if fnmatch.fnmatchcase(basename, TOP20_GLOB):
            return key
    return None


def _find_top20_object_key(
    output_dir: str, bucket_name: str, obs_server: str
) -> str:
    """Return the OBS object key for the top20 file under the network dir.

    Tries the obsfs glob first; falls back to the OBS SDK listObjects
    helper when obsfs is not mounted. Raises
    ``ChainTop20MissingError`` when no file matches the glob in either
    source.

    Args:
        output_dir: Network envelope's output directory.
        bucket_name: OBS bucket name used by the network config.
        obs_server: OBS endpoint for the SDK fallback.

    Returns:
        OBS object key suitable for ``download_obs_file``.

    Raises:
        ChainTop20MissingError: When neither obsfs nor the SDK find
            a matching file.
    """
    def _obsfs_action() -> str:
        basename = _obsfs_top20_match(output_dir, bucket_name)
        if basename is None:
            raise FileNotFoundError("no obsfs match")
        return _build_top20_object_key(
            output_dir, basename, bucket_name
        )

    def _sdk_action() -> str:
        key = _sdk_top20_match(output_dir, bucket_name, obs_server)
        if key is None:
            raise FileNotFoundError("no SDK match")
        return key

    try:
        return obsfs_or_sdk(_obsfs_action, _sdk_action)
    except (OSError, ValueError) as exc:
        raise ChainTop20MissingError(
            output_dir=output_dir,
            filename=TOP20_GLOB,
            message=(
                f"no file matching {TOP20_GLOB!r} under network "
                f"output_dir; last error: {exc}"
            ),
        ) from exc


def _scratch_dir_for(user_id: Optional[str]) -> Path:
    """Return a fresh scratch dir for the chain's top-20 CSV download.

    Args:
        user_id: Optional user identifier for the run identity.

    Returns:
        Path to a newly created scratch directory under the deepgenome
        config's ``DEEPGENOME_OUT`` root. The directory is created on
        disk by this call.
    """
    scratch_root = Path(DeepGenomeConfig().DEEPGENOME_OUT)
    run = RunIdentity.create(user_id=user_id, scope="chain")
    target = scratch_root / run.run_id
    target.mkdir(parents=True, exist_ok=True)
    return target


async def _download_top20_csv(
    output_dir: str, scratch_dir: Path
) -> Path:
    """Download the network agent's top20 gene CSV to the local scratch.

    Locates the file under the network output dir via the obsfs glob
    (with an SDK-listObjects fallback), then calls
    ``download_obs_file`` to materialize a local copy. When obsfs is
    mounted, ``download_obs_file`` returns the obsfs path directly
    with no extra download cost.

    Args:
        output_dir: Network envelope's output directory.
        scratch_dir: Local directory used as the ``server_dir`` for
            the OBS SDK download fallback.

    Returns:
        Local path of the downloaded top20 CSV.

    Raises:
        ChainTop20MissingError: When the file is missing, the OBS
            object key is empty, or the SDK download fails.
    """
    network_config = GeneNetworkConfig()
    object_key = _find_top20_object_key(
        output_dir,
        network_config.BUCKET_NAME,
        network_config.OBS_SERVER,
    )
    if not object_key:
        raise ChainTop20MissingError(
            output_dir=output_dir,
            filename=TOP20_GLOB,
            message=(
                "network output_dir did not produce a usable OBS "
                "object key"
            ),
        )
    sensitive = get_sensitive_config()
    access_key_id, secret_access_key = sensitive.obs_credentials()
    try:
        local_path = await download_obs_file(
            obs_file=object_key,
            server_dir=str(scratch_dir),
            obs_server=network_config.OBS_SERVER,
            bucket_name=network_config.BUCKET_NAME,
            part_size=network_config.PART_SIZE,
            task_num=network_config.TASK_NUM,
            max_retries=network_config.MAX_RETRIES,
            max_concurrency=network_config.MAX_CONCURRENCY,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )
    except OSError as exc:
        raise ChainTop20MissingError(
            output_dir=output_dir,
            filename=TOP20_GLOB,
            message=f"OBS download failed: {exc}",
        ) from exc
    csv_path = Path(local_path)
    if not csv_path.is_file():
        raise ChainTop20MissingError(
            output_dir=output_dir,
            filename=TOP20_GLOB,
            message="downloaded path is not a regular file",
        )
    return csv_path


async def network_to_deep_genome_chain(
    species: str,
    to_id: str,
    user_id: Optional[str] = None,
    batch: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the network analysis, parse top20, dispatch 20 deepgenome runs.

    The function blocks on the network analysis (which in turn blocks
    on the upstream ``construct_network.py`` analyst script), locates
    and downloads the resulting top-20 gene list CSV (matching the
    ``*top20_gene.csv`` glob) to a run-scoped scratch directory, parses
    up to 20 gene IDs, then submits one ``gene_function`` per gene in
    parallel via ``asyncio.gather(..., return_exceptions=True)`` — the
    same gather pattern used by ``agents/review/report.py`` and
    ``agents/knowledge/retrieval.py``.

    Args:
        species: Latin species name in lowercase with spaces (e.g.,
            ``"oryza sativa"``). Must be a key in ``SPECIES_TO_CODE``.
        to_id: Trait Ontology identifier for the target phenotype
            (e.g., ``"TO:0000207"``).
        user_id: Optional user identifier for path / OBS ownership.
        batch: Whether this is a batch submission; forwarded to
            ``network_analysis``.
        **kwargs: Optional keyword overrides forwarded to
            ``network_analysis`` and to each ``gene_function`` call.
            Keys matching the network/deepgenome config field names
            (e.g., ``output_dir``, ``obs_server``, ``bucket_name``,
            ``deepgenome_data``, ``deepgenome_out``, ``prompt_file``,
            ``user_id``, ``batch``, ``max_poll``, ``max_keys``,
            ``max_concurrency``, ``marker``, ``download_path``,
            ``access_key_id``, ``secret_access_key``) are passed
            through. MCP-handler-only fields (``dialog_id``,
            ``subject_id``, ``need_insight``, ``epic_type``,
            ``create_task_url``, ``update_task_url``, ``database_url``,
            ``workspace_id``) are stripped because this entry is a
            Python-level caller, not an MCP request.

    Returns:
        Dict with keys:
            ``network_envelope`` — submit envelope from
            ``network_analysis`` (contains ``task_id``,
            ``output_dir``, ``network_task``).
            ``top20_csv_path`` — local path of the downloaded CSV.
            ``gene_ids`` — list of gene IDs parsed from the CSV, in
            source order, length up to 20.
            ``deep_genome_envelopes`` — list of per-gene submit
            envelopes or ``Exception`` instances from
            ``asyncio.gather(..., return_exceptions=True)``, in gene
            order. Each envelope contains ``task_id``, ``output_dir``,
            ``compute_resource``; callers can
            ``sum(isinstance(x, Exception) for x in ...)`` to count
            failures.

    Raises:
        ChainTop20MissingError: When the network envelope's
            ``output_dir`` lacks a CSV matching ``*top20_gene.csv``,
            the CSV is unreadable, or the file has no recognizable
            gene-id column.
        KeyError: When ``species`` is not in ``SPECIES_TO_CODE``.
    """
    if species not in SPECIES_TO_CODE:
        raise KeyError(
            f"species {species!r} is not in SPECIES_TO_CODE; "
            "extend the map or use a different entry point."
        )

    forwarded_kwargs = _filter_chain_kwargs(kwargs)

    network_envelope = await network_analysis(
        species=species,
        to_id=to_id,
        user_id=user_id,
        batch=batch,
        **forwarded_kwargs,
    )

    nested_task = network_envelope.get("network_task") or {}
    output_dir = nested_task.get("output_dir") or network_envelope.get(
        "output_dir", ""
    )
    if not output_dir:
        raise ChainTop20MissingError(
            output_dir="",
            filename=TOP20_GLOB,
            message="network envelope did not surface an output_dir",
        )

    scratch_dir = _scratch_dir_for(user_id)
    csv_path = await _download_top20_csv(output_dir, scratch_dir)
    gene_ids = _parse_top20_gene_ids(csv_path)
    if not gene_ids:
        logger.warning(
            "network chain parsed 0 gene IDs from %s", csv_path
        )

    species_code = SPECIES_TO_CODE[species]
    deep_kwargs = _filter_chain_kwargs(kwargs)
    coros = [
        gene_function(
            species_code=species_code,
            gene_id=gene_id,
            user_id=user_id,
            **deep_kwargs,
        )
        for gene_id in gene_ids
    ]
    envelopes: List[Any] = []
    if coros:
        envelopes = list(
            await asyncio.gather(*coros, return_exceptions=True)
        )

    return {
        "network_envelope": network_envelope,
        "top20_csv_path": str(csv_path),
        "gene_ids": gene_ids,
        "deep_genome_envelopes": envelopes,
    }


_CHAIN_DROPPED_KWARGS: Final[frozenset[str]] = frozenset(
    {
        "dialog_id",
        "subject_id",
        "need_insight",
        "epic_type",
        "create_task_url",
        "update_task_url",
        "database_url",
        "workspace_id",
    }
)


def _filter_chain_kwargs(kwargs: Mapping[str, Any]) -> Dict[str, Any]:
    """Strip MCP-handler-only keys from a kwargs mapping.

    Args:
        kwargs: Caller-supplied keyword arguments.

    Returns:
        New dict with chain-incompatible keys removed; everything else
        (including unknown keys) is forwarded so call-site overrides
        of any field in the agent config field maps still work.
    """
    return {key: value for key, value in kwargs.items()
            if key not in _CHAIN_DROPPED_KWARGS}
