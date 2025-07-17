# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import asyncio
import json
import time
from pathlib import Path
from threading import Thread
from traceback import format_exc
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid1

from obs import GetObjectHeader, ObsClient
from mcp.shared.exceptions import McpError

from .analyst_agents import submit, wait_for_completion
from .chat_agents import phyto_chat
from .config.defaults import DeepGenomeConfig
from .config.settings import SensitiveConfig
from .data_agents import nl2sql
from .knowledge_agents import multi_retrieve
from .task_manager import create_task, TaskManager, update_task
from .utils import get_prompt

SPECIES_CODE_MAP = {
    'osa': 'rice (Oryza sativa)',
    'hvu': 'barley (Hordeum vulgare)',
    'ttu': 'durum (Triticum turgidum)',
    'cqu': 'quinoa (Chenopodium quinoa)',
    'obr': 'wild (Oryza brachyantha)',
    'dex': 'white (Digitaria exilis)',
    'han': 'sunflower (Helianthus annuus)',
    'dca': 'carrot (Daucus carota)',
    'pvu': 'common (Phaseolus vulgaris)',
    'ccan': 'coffee (Coffea canephora)',
    'aof': 'garden (Asparagus officinalis)',
    'bol': 'Brassica oleracea',
    'rch': 'rose (Rosa chinensis)',
    'lpe': 'Lolium perenne',
    'atr': 'Amborella trichopoda',
    'esa': 'saltwater (Eutrema salsugineum)',
    'zma': 'maize (Zea mays)',
    'bna': 'oilseed (Brassica napus)',
    'bra': 'Brassica rapa',
    'ghi': 'upland (Gossypium hirsutum)',
    'gra': 'cotton (Gossypium raimondii)',
    'mtr': 'barrel (Medicago truncatula)',
    'stu': 'potato (Solanum tuberosum)',
    'sbi': 'sorghum (Sorghum bicolor)',
    'sit': 'foxtail (Setaria italica)',
    'sce': 'rye (Secale cereale)',
    'tdi': 'emmer (Triticum dicoccoides)',
    'sly': 'tomato (Solanum lycopersicum)',
    'cme': 'muskmelon (Cucumis melo)',
    'psa': 'garden (Pisum sativum)',
    'oeu': 'common (Olea europaea)',
    'ach': 'kiwi (Actinidia chinensis)',
    'cla': 'watermelon (Citrullus lanatus)',
    'vvi': 'grape (Vitis vinifera)',
    'tca': 'cacao (Theobroma cacao)',
    'smo': 'Selaginella moellendorffii',
    'qlo': 'Quercus lobata',
    'bdi': 'Brachypodium distachyon',
    'cav': 'Corylus avellana',
    'egr': 'Eucalyptus grandis',
    'mpo': 'liverwort (Marchantia polymorpha)',
    'ssp': 'sugarcane (Saccharum spontaneum)',
    'pso': 'opium (Papaver somniferum)',
    'cre': 'Chlamydomonas reinhardtii',
    'gma': 'soybean (Glycine max)',
    'mes': 'cassava (Manihot esculenta)',
    'can': 'pepper (Capsicum annuum)',
    'csa': 'cucumber (Cucumis sativus)',
    'lsa': 'lettuce (Lactuca sativa)',
    'aco': 'pineapple (Ananas comosus)',
    'mac': 'banana (Musa acuminata)',
    'ppe': 'peach (Prunus persica)',
    'ptr': 'black (Populus trichocarpa)',
    'ath': 'thale (Arabidopsis thaliana)',
    'ata': 'rough-spike (Aegilops tauschii)',
    'bvu': 'suger (Beta vulgaris)',
    'ccl': 'citrus (Citrus clementina)',
    'svi': 'green (Setaria viridis)',
    'ecu': 'weeping (Eragrostis curvula)',
    'pha': "Hall's (Panicum hallii)",
    'aly': 'Arabidopsis lyrata',
    'tpr': 'red (Trifolium pratense)',
    'ppa': 'Physcomitrium patens',
    'cbr': 'Chara braunii',
    'tae': 'wheat (Triticum aestivum)',
}
dgc = DeepGenomeConfig()
sc = SensitiveConfig().load()
_manager_cache = {}


def _get_manager():
    if 'instance' not in _manager_cache:
        _manager_cache['instance'] = TaskManager()
    return _manager_cache['instance']


async def gene_network(species_code: str,
                       gene_id: str,
                       workspace_id: str = dgc.WORKSPACE_ID,
                       subject_id: str = dgc.SUBJECT_ID,
                       dialog_id: str = dgc.DIALOG_ID,
                       need_insight: bool = dgc.NEED_INSIGHT,
                       timeout: float = dgc.TIMEOUT,
                       retriable_codes: List[int] = dgc.RETRIABLE_CODES,
                       max_retries: int = dgc.MAX_RETRIES,
                       ) -> Tuple[List[Tuple[str]]]:
    """Retrieve and categorize gene relationships for a given gene and species.

    This function queries a database using two parallel `nl2sql` calls to find:
    1. Homologous genes (orthologs and paralogs) for the input `gene_id`.
    2. Genes that interact with the input `gene_id`.

    Orthologs are homologous genes found in different species. Paralogs are
    homologous genes found within the same input `species_code`. Interactions
    are also specific to the input `species_code`.

    Args:
        species_code: The species code for the primary gene. This is used to
            differentiate paralogs (same species) from orthologs (different
            species) and to scope interactions.
        gene_id: The identifier of the primary gene for which to find
            orthologs, paralogs, and interactions.
        workspace_id: Identifier for the workspace containing the data, passed
            to the `nl2sql` function. Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
            Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function. Defaults to `''`.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
            Defaults to `NEED_INSIGHT`.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls. Defaults to `MAX_RETRIES`.

    Returns:
        Tuple[List[Tuple[str, str]], List[Tuple[str, str]],
                List[Tuple[str, str]]]:
            A tuple containing three sorted lists:
            - `gene_orthologs_list`: A list of `(species_code, gene_id)`
              tuples for orthologous genes.
            - `gene_paralogs_list`: A list of `(species_code, gene_id)`
              tuples for paralogous genes (where `species_code` is the
              input `species_code`).
            - `gene_interaction_list`: A list of `(species_code, gene_id)`
              tuples for interacting genes (where `species_code` is the
              input `species_code`).

    Raises:
        McpError: If any of the `nl2sql` calls fail after all retry attempts.
    """
    message_content = ('What is the query_gene_id_10, query_species_10, '
                       'homology_gene_id_10 and homology_species_10 whose '
                       f'query_gene_id_10 is {gene_id}.')
    homology_task = nl2sql(
        message_content=message_content,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=True,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    message_content = ("Give the query_gene_id_11, query_protein_11, "
                       "interact_gene_id_11 and interact_protein_11 where "
                       f"query_gene_id_11 is '{gene_id}' or "
                       f"interact_gene_id_11 is '{gene_id}'.")
    interaction_task = nl2sql(
        message_content=message_content,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=True,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    gene_homology_response, gene_interaction_response = await asyncio.gather(
        homology_task,
        interaction_task
    )
    gene_orthologs_set, gene_paralogs_set = set(), set()
    for gene_homology in gene_homology_response['data']:
        if gene_homology[3] == species_code:
            gene_paralogs_set.add((gene_homology[3], gene_homology[2]))
        else:
            gene_orthologs_set.add((gene_homology[3], gene_homology[2]))
    gene_orthologs_list = sorted(gene_orthologs_set)
    gene_paralogs_list = sorted(gene_paralogs_set)
    gene_interaction_set = set()
    for gene_interaction in gene_interaction_response['data']:
        if gene_interaction[0] == gene_id:
            gene_interaction_set.add((species_code, gene_interaction[2]))
        elif gene_interaction[2] == gene_id:
            gene_interaction_set.add((species_code, gene_interaction[0]))
    gene_interaction_list = sorted(gene_interaction_set)
    return gene_orthologs_list, gene_paralogs_list, gene_interaction_list


async def gene_symbol(species_code: str,
                      gene_id: str,
                      workspace_id: str = dgc.WORKSPACE_ID,
                      subject_id: str = dgc.SUBJECT_ID,
                      dialog_id: str = dgc.DIALOG_ID,
                      need_insight: bool = dgc.NEED_INSIGHT,
                      timeout: float = dgc.TIMEOUT,
                      retriable_codes: List[int] = dgc.RETRIABLE_CODES,
                      max_retries: int = dgc.MAX_RETRIES,
                      semaphore: Optional[asyncio.Semaphore] = None
                      ) -> List[str]:
    """Retrieve gene symbols for a specific gene ID and species code.

    This function queries a database using the `nl2sql` service to find
    gene symbols associated with the provided `gene_id` and `species_code`.
    It parses the response from `nl2sql`, expecting a specific structure,
    and extracts gene symbols. It can handle symbols that are pipe-separated
    or comma-separated within a single field. An optional semaphore can be
    used to limit concurrency if this function is called multiple times.

    Args:
        species_code: The species code for the gene for which symbols are
            being retrieved.
        gene_id: The identifier of the gene for which symbols are being
            retrieved.
        workspace_id: Identifier for the workspace containing the data, passed
            to the `nl2sql` function. Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
            Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function. Defaults to an empty string.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
            Defaults to `NEED_INSIGHT`.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls. Defaults to `MAX_RETRIES`.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `nl2sql` calls if this function is called
            multiple times concurrently. Defaults to `None`.

    Returns:
        A list of gene symbols associated with the given `gene_id` and
        `species_code`. Returns an empty list if no symbols are found or
        if the data format from the `nl2sql` service is not as expected.

    Raises:
        McpError: If the underlying `nl2sql` call fails after all retry
        attempts.
    """
    async def get_gene_symbol() -> Dict:
        message_content = (f'List all the columns whose gene_id_1 is {gene_id}'
                           f' and species_code_1 is {species_code}?')
        gene_symbol_response = await nl2sql(
            message_content=message_content,
            workspace_id=workspace_id,
            subject_id=subject_id,
            dialog_id=dialog_id,
            need_insight=need_insight,
            simplify_response=False,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )
        gene_symbol_list = []
        if 'query_data' in gene_symbol_response:
            if len(gene_symbol_response['query_data']) == 2:
                for eachcell in gene_symbol_response['query_data'][1]:
                    caption = eachcell['caption']
                    cell_raw_value = eachcell['cell_raw_value']
                    if cell_raw_value and caption == 'symbol_1':
                        if '|' in cell_raw_value:
                            gene_symbol_list.extend(
                                set(cell_raw_value.split('|')))
                        elif ',' in cell_raw_value:
                            gene_symbol_list.extend(
                                set(cell_raw_value.split(',')))
                        else:
                            gene_symbol_list.append(cell_raw_value)
                return gene_symbol_list
            return []
        else:
            return []

    if semaphore is not None:
        async with semaphore:
            return await get_gene_symbol()
    else:
        return await get_gene_symbol()


async def gene_annotation(species_code: str,
                          gene_id: str,
                          workspace_id: str = dgc.WORKSPACE_ID,
                          subject_id: str = dgc.SUBJECT_ID,
                          dialog_id: str = dgc.DIALOG_ID,
                          need_insight: bool = dgc.NEED_INSIGHT,
                          timeout: float = dgc.TIMEOUT,
                          retriable_codes: List[int] = dgc.RETRIABLE_CODES,
                          max_retries: int = dgc.MAX_RETRIES,
                          semaphore: Optional[asyncio.Semaphore] = None
                          ) -> Dict[str, Union[str, List[str]]]:
    """Retrieve various annotations for a specific gene ID and species code.

    This function concurrently queries a database using multiple `nl2sql` calls
    to fetch the gene's description, Gene Ontology (GO) terms (ID and name),
    InterPro domains (ID and name), and MapMan classifications (ID and
    description). The results are aggregated into a dictionary. An optional
    semaphore can be used to limit the concurrency of these `nl2sql` calls.

    Args:
        species_code: The species code for the gene for which annotations
            are being retrieved.
        gene_id: The identifier of the gene for which annotations are being
            retrieved.
        workspace_id: Identifier for the workspace containing the data, passed
            to the `nl2sql` function. Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
            Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function. Defaults to an empty string.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
            Defaults to `NEED_INSIGHT`.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls. Defaults to `MAX_RETRIES`.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `nl2sql` calls if this function is called
            multiple times concurrently. Defaults to `None`.

    Returns:
        A dictionary containing various annotations for the specified gene.
        The dictionary has the following keys:
        - 'description' (str): A textual description of the gene.
        - 'go' (List[str]): A list containing the Gene Ontology ID and name
          (e.g., `['GO_ID', 'GO_NAME']`).
        - 'interpro' (List[str]): A list containing the InterPro ID and name
          (e.g., `['INTERPRO_ID', 'INTERPRO_NAME']`).
        - 'mapman' (List[str]): A list containing the MapMan ID and its
          description (e.g., `['MAPMAN_ID', 'MAPMAN_DESCRIPTION']`).
        If a particular annotation type is not found, its corresponding value
        might be based on the `nl2sql` response for empty data (e.g., an
        empty list or a default string, depending on `nl2sql` behavior).

    Raises:
        McpError: If any of the underlying `nl2sql` calls fail after all
            retry attempts.
        IndexError: If the `nl2sql` response data structure is not as
            expected (e.g., missing expected elements in the 'data' list).
    """
    async def get_gene_annotation() -> Dict:
        query_list = [
            f'What is the description_6 whose gene_id_6 is {gene_id} and '
            f'species_code_6 is {species_code}?',
            f'What is go_id_7 and go_name_7 whose gene_id_7 is {gene_id} and '
            f'species_code_7 is {species_code}?',
            'What is the interpro_id_8 and interpro_name_8 whose '
            f'gene_id_8 is {gene_id} and species_code_8 is {species_code}?',
            'What is the mapman_9 and mapman_description_9 whose '
            f'gene_id_9 is {gene_id} and species_code_9 is {species_code}?',
        ]
        responses = await asyncio.gather(
            *(nl2sql(
                message_content=gene_query,
                workspace_id=workspace_id,
                subject_id=subject_id,
                dialog_id=dialog_id,
                need_insight=need_insight,
                simplify_response=True,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            ) for gene_query in query_list)
        )
        gene_anno_dict = {}
        if responses[0]['data']:
            gene_anno_dict.update({'description': responses[0]['data'][0][0]})
        if responses[1]['data']:
            gene_anno_dict.update({'go': responses[1]['data']})
        if responses[2]['data']:
            gene_anno_dict.update({'interpro': responses[2]['data']})
        if responses[3]['data']:
            gene_anno_dict.update({'mapman': responses[3]['data']})
        return gene_anno_dict

    if semaphore is not None:
        async with semaphore:
            return await get_gene_annotation()
    else:
        return await get_gene_annotation()


def network_to_string(gene_network_list: list,
                      species_gene_symbol_dict: dict,
                      species_gene_anno_dict: dict,
                      network_type: str,
                      top_n: int = 10):
    """Formats gene network information into a string.

    This function takes a list of genes in a network and their associated
    symbols and annotations, and formats this information into a model-readable
    string. The string includes the description of each gene and a summary of
    the top N enriched GO, InterPro, and MapMan terms for the entire network.

    Args:
        gene_network_list (list): A list of tuples, where each tuple
                                  represents a gene in the network and
                                  contains the species code and gene ID.
        species_gene_symbol_dict (dict): A dictionary mapping a
                                         (species_code, gene_id) tuple to a
                                         list of gene symbols.
        species_gene_anno_dict (dict): A dictionary mapping a
                                       (species_code, gene_id) tuple to a
                                       dictionary of gene annotations.
        network_type (str): The type of the network (e.g., "Orthologous",
                            "Paralogous"). This is used in the output string.
        top_n (int, optional): The number of top enriched terms to include in
                               the summary. Defaults to 10.

    Returns:
        str: A formatted string containing the gene network information, or a
             string indicating that no genes of the specified network type
             were found.
    """
    if gene_network_list:
        network_string = ''
        go_id_dict, ip_id_dict, mm_id_dict = {}, {}, {}
        go_count_dict, ip_count_dict, mm_count_dict = {}, {}, {}
        for species_gene in gene_network_list:
            if species_gene in species_gene_symbol_dict:
                symbol_string = '|'.join(species_gene_symbol_dict[
                    species_gene]).replace('\n', '|')
            else:
                symbol_string = species_gene[1]
            if species_gene in species_gene_anno_dict:
                gene_anno = species_gene_anno_dict[species_gene]
                if 'description' in gene_anno:
                    description_string = gene_anno['description']
                else:
                    description_string = ''
                if 'go' in gene_anno:
                    for go_list in gene_anno['go']:
                        go_id_dict.update({go_list[0]: go_list[1]})
                        if go_list[0] in go_count_dict:
                            go_count_dict[go_list[0]] += 1
                        else:
                            go_count_dict.update({go_list[0]: 1})
                if 'interpro' in gene_anno:
                    for ip_list in gene_anno['interpro']:
                        ip_id_dict.update({ip_list[0]: ip_list[1]})
                        if ip_list[0] in ip_count_dict:
                            ip_count_dict[ip_list[0]] += 1
                        else:
                            ip_count_dict.update({ip_list[0]: 1})
                if 'mapman' in gene_anno:
                    for mm_list in gene_anno['mapman']:
                        mm_id_dict.update({mm_list[0]: mm_list[1]})
                        if mm_list[0] in mm_count_dict:
                            mm_count_dict[mm_list[0]] += 1
                        else:
                            mm_count_dict.update({mm_list[0]: 1})
            else:
                description_string = ''
            if description_string:
                network_string += f'{SPECIES_CODE_MAP[species_gene[0]]}: '
                network_string += f'{symbol_string}: {description_string}\n'
        go_sorted_ids = sorted(go_count_dict.items(), key=lambda x: x[1],
                               reverse=True)[:top_n]
        go_all_string = '; '.join([go_id_dict[id] for id, count in
                                   go_sorted_ids if id in go_id_dict])
        network_string += f'{network_type} genes TOP {top_n} '
        network_string += f'GO enrichment results: {go_all_string}\n'
        ip_sorted_ids = sorted(ip_count_dict.items(), key=lambda x: x[1],
                               reverse=True)[:top_n]
        ip_all_string = '; '.join([ip_id_dict[id] for id, count in
                                   ip_sorted_ids if id in ip_id_dict])
        network_string += f'{network_type} genes TOP {top_n} '
        network_string += f'InterPro enrichment results: {ip_all_string}\n'
        mm_sorted_ids = sorted(mm_count_dict.items(), key=lambda x: x[1],
                               reverse=True)[:top_n]
        mm_all_string = '; '.join([mm_id_dict[id] for id, count in
                                   mm_sorted_ids if id in mm_id_dict])
        network_string += f'{network_type} genes TOP {top_n} '
        network_string += f'MapMan enrichment results: {mm_all_string}\n'
        return network_string
    return f'No {network_type} genes'


async def gene_retrieve(
    species: str,
    gene_symbol_list: list,
    retrieve_url: str = dgc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = dgc.REPO_ID_DICT,
    page_num: int = dgc.PAGE_NUM,
    filter_string: Optional[str] = dgc.FILTER_STRING,
    extra_repo_ids: Optional[List[str]] = dgc.EXTRA_REPO_IDS,
    rerank_url: str = dgc.RERANK_URL,
    rerank_batch_size: int = dgc.RERANK_BATCH_SIZE,
    score_threshold: float = dgc.SCORE_THRESHOLD,
    top_n: int = dgc.TOP_N,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Retrieve documents related to a list of gene symbols for a given
        species.

    This function concurrently performs document retrieval using
    `multi_retrieve` for each gene symbol in the provided `gene_symbol_list`.
    Additionally, it performs a retrieval for a newline-separated
    concatenation of all gene symbols. Retrieval is performed with
    `scope='both'`.

    The document lists from all successful retrieval calls are merged, sorted
    by relevance score in descending order, and the final list is truncated to
    the `top_n` documents. An optional semaphore can be used to limit the
    concurrency of `multi_retrieve` calls.

    Args:
        species: The species associated with the gene symbols, used to form
            part of the query for `multi_retrieve`.
        gene_symbol_list: A list of gene symbols to retrieve documents for.
            Note: This list is temporarily extended internally to include a
            newline-separated string of all its original elements for an
            additional combined query.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval, passed to
            `multi_retrieve`. Defaults to `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results from each
            repository, passed to `multi_retrieve`. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval, passed to `multi_retrieve`.
            Defaults to `FILTER_STRING`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the document retrieval, passed to `multi_retrieve`.
            Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval by `multi_retrieve`.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The number of top-scoring documents to retrieve from each
            individual `multi_retrieve` call, and also the number of
            top-scoring documents to return in the final merged and sorted
            list. Defaults to `TOP_N`.
        timeout: Request timeout in seconds for the underlying `multi_retrieve`
            API calls. Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `multi_retrieve` API calls.
            Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying
            `multi_retrieve` API calls. Defaults to `MAX_RETRIES`.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `multi_retrieve` calls if this function is called
            multiple times concurrently. Defaults to `None`.

    Returns:
        A dictionary containing the retrieval results. If `gene_symbol_list`
        is empty, an empty dictionary `{}` is returned. Otherwise, the
        dictionary will contain:
        - "doc_list" (List[Dict]): A list of document dictionaries, sorted by
          score in descending order, and truncated to `top_n` documents.
          Each document dictionary is expected to conform to the output of
          `multi_retrieve`.
        - "total" (int): A hardcoded integer value of `10000`, likely
          representing a conceptual total count or placeholder.
        If individual `multi_retrieve` calls encounter exceptions, they are
        caught, and their results are excluded from the `merged_docs`.
    """
    async def get_gene_retrieve():
        if gene_symbol_list:
            tasks = []
            gene_symbol_list.append('\n'.join(gene_symbol_list))
            for each_symbol in gene_symbol_list:
                retrieve_query = species + '\n' + each_symbol
                tasks.append(multi_retrieve(
                    user_query=retrieve_query,
                    retrieve_url=retrieve_url,
                    repo_id_dict=repo_id_dict,
                    page_num=page_num,
                    filter_string=filter_string,
                    scope='both',
                    extra_repo_ids=extra_repo_ids,
                    rerank_url=rerank_url,
                    rerank_batch_size=rerank_batch_size,
                    score_threshold=score_threshold,
                    top_n=top_n,
                    timeout=timeout,
                    retriable_codes=retriable_codes,
                    max_retries=max_retries,
                ))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            merged_docs = []
            for result in results:
                if isinstance(result, dict) and 'doc_list' in result:
                    merged_docs.extend(result['doc_list'])
            sorted_docs = sorted(merged_docs,
                                 key=lambda x: x['score'],
                                 reverse=True)
            if top_n is not None and top_n > 0:
                sorted_docs = sorted_docs[:top_n]
            return {
                "doc_list": sorted_docs,
                "total": 10000,
            }
        return {}

    if semaphore is not None:
        async with semaphore:
            return await get_gene_retrieve()
    else:
        return await get_gene_retrieve()


async def async_gene_function(
    species_code: str,
    gene_id: str,
    create_task_url: str = dgc.CREATE_TASK_URL,
    update_task_url: str = dgc.UPDATE_TASK_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
    retrieve_url: str = dgc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = dgc.REPO_ID_DICT,
    page_num: int = dgc.PAGE_NUM,
    filter_string: Optional[str] = dgc.FILTER_STRING,
    extra_repo_ids: Optional[List[str]] = dgc.EXTRA_REPO_IDS,
    rerank_url: str = dgc.RERANK_URL,
    rerank_batch_size: int = dgc.RERANK_BATCH_SIZE,
    score_threshold: float = dgc.SCORE_THRESHOLD,
    top_n: int = dgc.TOP_N,
    prompt_file: str = dgc.PROMPT_FILE,
    prompt_path: str = dgc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = dgc.FREQUENCY_PENALTY,
    max_tokens: int = dgc.MAX_TOKENS,
    n: int = dgc.N,
    presence_penalty: float = dgc.PRESENCE_PENALTY,
    reasoning_effort: str = dgc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = dgc.RESPONSE_FORMAT,
    stream: bool = dgc.STREAM,
    temperature: float = dgc.TEMPERATURE,
    top_p: float = dgc.TOP_P,
    user: str = dgc.USER,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_concurrency: int = dgc.MAX_CONCURRENCY,
    use_data_agent: bool = True,
    use_analyst_agent: bool = True,
    direct_return: bool = False,
) -> Dict[str, Any]:
    """Determine and summarize the function of a specified gene.

    This function orchestrates a series of asynchronous operations to gather
    information about a given gene and its network, then uses a large language
    model (`phyto_chat`) to generate a summary of its function.
    The process involves:
    1. Retrieving gene network information (orthologs, paralogs, interactors)
       using `gene_network`.
    2. Fetching gene symbols for the primary gene and its network neighbors
       using `gene_symbol`.
    3. Obtaining gene annotations (description, GO, InterPro, MapMan) for these
       genes using `gene_annotation`.
    4. Retrieving relevant documents from literature repositories for these
       genes and their symbols using `gene_retrieve`.
    5. Constructing a prompt with the species, primary gene symbols, and
       retrieved documents for the primary gene.
    6. Calling `phyto_chat` to generate a textual summary of the gene's
       function.
    7. Augmenting the `phyto_chat` response with the full list of retrieved
       documents.

    A semaphore is used to limit the concurrency of `gene_symbol`,
    `gene_annotation`, and `gene_retrieve` calls.

    Args:
        species_code: The species code for the primary gene of interest.
        gene_id: The identifier of the primary gene of interest.
        workspace_id: Identifier for the workspace containing the data,
            passed to `gene_network`, `gene_symbol`, and `gene_annotation`
            for database queries. Defaults to `WORKSPACE_ID`.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to `gene_network`, `gene_symbol`, and
            `gene_annotation`. Defaults to `SUBJECT_ID`.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to `gene_network`, `gene_symbol`, and `gene_annotation`.
            Defaults to an empty string.
        need_insight: Flag indicating whether to generate insights, passed to
            `gene_network`, `gene_symbol`, and `gene_annotation`.
            Defaults to `NEED_INSIGHT`.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval, passed to
            `gene_retrieve`. Defaults to `REPO_ID_DICT`.
        page_num: Pagination page number for retrieval results, passed to
            `gene_retrieve`. Defaults to `PAGE_NUM`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval, passed to `gene_retrieve`.
            Defaults to `FILTER_STRING`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in document retrieval, passed to `gene_retrieve`.
            Defaults to `EXTRA_REPO_IDS`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval, passed to `gene_retrieve`.
            Defaults to `SCORE_THRESHOLD`.
        top_n: The number of top-scoring documents to retrieve, passed to
            `gene_retrieve`. It affects retrieval for each gene symbol and
            the documents considered for the primary gene's context.
            Defaults to `TOP_N`.
        prompt_file: Path to the prompt template file used by `phyto_chat` for
            generating the gene function summary. Defaults to `PROMPT_FILE`.
        prompt_path: Path or key within the `prompt_file` to retrieve the
            specific system prompt for `phyto_chat`. Defaults to `PROMPT_PATH`.
        api_key: API key for authentication with the Phyto model.
            Defaults to `API_KEY`.
        base_url: Base URL of the Phyto API service (`phyto_chat`).
            Defaults to `BASE_URL`.
        model: Identifier of the Phyto model to use via `phyto_chat`.
            Defaults to `MODEL_ID`.
        frequency_penalty: Penalty for token repetition (-2.0 to 2.0) for
            `phyto_chat`. Defaults to `FREQUENCY_PENALTY`.
        max_tokens: Maximum number of tokens to generate by `phyto_chat`.
            Defaults to `MAX_TOKENS`.
        n: Number of summary choices to generate by `phyto_chat`.
            Defaults to `N`.
        presence_penalty: Penalty for new tokens (-2.0 to 2.0) for
            `phyto_chat`. Defaults to `PRESENCE_PENALTY`.
        reasoning_effort: Specifies the reasoning effort for `phyto_chat`.
            Defaults to `REASONING_EFFORT`.
        response_format: Specifies the desired output format for `phyto_chat`.
            Defaults to `RESPONSE_FORMAT`.
        stream: Enable real-time token streaming output for `phyto_chat`.
            Defaults to `STREAM`.
        temperature: Controls randomness (0.0-1.0) for `phyto_chat`.
            Defaults to `TEMPERATURE`.
        top_p: Nucleus sampling threshold (0.0-1.0) for `phyto_chat`.
            Defaults to `TOP_P`.
        user: Unique session identifier for the end-user,
            passed to `phyto_chat`. Defaults to `USER`.
        timeout: Request timeout in seconds for all underlying asynchronous
            API calls (`gene_network`, `gene_symbol`, `gene_annotation`,
            `gene_retrieve`, `phyto_chat`). Defaults to `TIMEOUT`.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying API calls. Defaults to `RETRIABLE_CODES`.
        max_retries: Maximum number of retry attempts for underlying API calls.
            Defaults to `MAX_RETRIES`.
        max_concurrency: Maximum number of concurrent asynchronous operations
            (e.g., `gene_symbol`, `gene_annotation`, `gene_retrieve` calls)
            controlled by the internal semaphore.
            Defaults to `MAX_CONCURRENCY`.

    Returns:
        dict: The response dictionary from `phyto_chat`, which typically
        includes a 'choices' list with the generated summary of the gene's
        function. This dictionary is augmented with a 'doc_list' key
        (containing all documents retrieved for the primary gene and its
        network neighbors) and a 'total' key (a placeholder integer).
        Returns an empty dictionary if essential preliminary data (e.g.,
        symbols or documents for the primary gene) cannot be obtained.

    Raises:
        McpError: If any of the underlying asynchronous calls (`gene_network`,
            `gene_symbol`, `gene_annotation`, `gene_retrieve`, `phyto_chat`)
            fail after all retry attempts.
        KeyError: If a `species_code` (either the input `species_code` or one
            derived from `gene_network` results) is not found in the internal
            `SPECIES_CODE_MAP` when preparing data for `gene_retrieve` or the
            final prompt.
    """
    if not direct_return:
        manager = _get_manager()
        task_id = manager.create_task()
        _ = await create_task(
            url=create_task_url,
            server_id=task_id,
            server_status='running',
            tool_name='DeepGenomeAgent',
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries)

    async def gene_function(
        species_code: str,
        gene_id: str,
        workspace_id: str = dgc.WORKSPACE_ID,
        subject_id: str = dgc.SUBJECT_ID,
        dialog_id: str = dgc.DIALOG_ID,
        need_insight: bool = dgc.NEED_INSIGHT,
        retrieve_url: str = dgc.RETRIEVE_URL,
        repo_id_dict: Optional[Dict[str, int]] = dgc.REPO_ID_DICT,
        page_num: int = dgc.PAGE_NUM,
        filter_string: Optional[str] = dgc.FILTER_STRING,
        extra_repo_ids: Optional[List[str]] = dgc.EXTRA_REPO_IDS,
        rerank_url: str = dgc.RERANK_URL,
        rerank_batch_size: int = dgc.RERANK_BATCH_SIZE,
        score_threshold: float = dgc.SCORE_THRESHOLD,
        top_n: int = dgc.TOP_N,
        prompt_file: str = dgc.PROMPT_FILE,
        prompt_path: str = dgc.PROMPT_PATH,
        api_key: str = sc.API_KEY.get_secret_value(),
        base_url: str = sc.BASE_URL,
        model: str = sc.MODEL_ID,
        frequency_penalty: float = dgc.FREQUENCY_PENALTY,
        max_tokens: int = dgc.MAX_TOKENS,
        n: int = dgc.N,
        presence_penalty: float = dgc.PRESENCE_PENALTY,
        reasoning_effort: str = dgc.REASONING_EFFORT,
        response_format: Dict[str, Union[str, Dict]] = dgc.RESPONSE_FORMAT,
        stream: bool = dgc.STREAM,
        temperature: float = dgc.TEMPERATURE,
        top_p: float = dgc.TOP_P,
        user: str = dgc.USER,
        timeout: float = dgc.TIMEOUT,
        retriable_codes: List[int] = dgc.RETRIABLE_CODES,
        max_retries: int = dgc.MAX_RETRIES,
        max_concurrency: int = dgc.MAX_CONCURRENCY,
        use_data_agent: bool = True,
        use_analyst_agent: bool = True,
        direct_return: bool = False,
    ) -> Dict[str, Any]:
        """Determine and summarize the function of a specified gene.

        This function orchestrates a series of asynchronous operations to
        gather information about a given gene and its network, then uses a
        large language model (`phyto_chat`) to generate a summary of its
        function.
        The process involves:
        1. Retrieving gene network information (orthologs, paralogs,
        interactors) using `gene_network`.
        2. Fetching gene symbols for the primary gene and its network neighbors
        using `gene_symbol`.
        3. Obtaining gene annotations (description, GO, InterPro, MapMan) for
        these genes using `gene_annotation`.
        4. Retrieving relevant documents from literature repositories for these
        genes and their symbols using `gene_retrieve`.
        5. Constructing a prompt with the species, primary gene symbols, and
        retrieved documents for the primary gene.
        6. Calling `phyto_chat` to generate a textual summary of the gene's
        function.
        7. Augmenting the `phyto_chat` response with the full list of retrieved
        documents.

        A semaphore is used to limit the concurrency of `gene_symbol`,
        `gene_annotation`, and `gene_retrieve` calls.

        Args:
            species_code: The species code for the primary gene of interest.
            gene_id: The identifier of the primary gene of interest.
            workspace_id: Identifier for the workspace containing the data,
                passed to `gene_network`, `gene_symbol`, and `gene_annotation`
                for database queries. Defaults to `WORKSPACE_ID`.
            subject_id: Identifier for the specific database subject or schema
                to query against, passed to `gene_network`, `gene_symbol`, and
                `gene_annotation`. Defaults to `SUBJECT_ID`.
            dialog_id: Identifier for the current dialog or conversation
                session, passed to `gene_network`, `gene_symbol`, and
                `gene_annotation`. Defaults to an empty string.
            need_insight: Flag indicating whether to generate insights, passed
                to `gene_network`, `gene_symbol`, and `gene_annotation`.
                Defaults to `NEED_INSIGHT`.
            repo_id_dict: A dictionary mapping repository IDs (str) to their
                respective page sizes (int) for document retrieval, passed to
                `gene_retrieve`. Defaults to `REPO_ID_DICT`.
            page_num: Pagination page number for retrieval results, passed to
                `gene_retrieve`. Defaults to `PAGE_NUM`.
            filter_string: Optional filter criteria string for metadata
                filtering during document retrieval, passed to `gene_retrieve`.
                Defaults to `FILTER_STRING`.
            extra_repo_ids: Optional list of additional repository IDs to
                include in document retrieval, passed to `gene_retrieve`.
                Defaults to `EXTRA_REPO_IDS`.
            score_threshold: Minimum relevance score threshold applied during
                document retrieval, passed to `gene_retrieve`.
                Defaults to `SCORE_THRESHOLD`.
            top_n: The number of top-scoring documents to retrieve, passed to
                `gene_retrieve`. It affects retrieval for each gene symbol and
                the documents considered for the primary gene's context.
                Defaults to `TOP_N`.
            prompt_file: Path to the prompt template file used by `phyto_chat`
                for generating the gene function summary.
                Defaults to `PROMPT_FILE`.
            prompt_path: Path or key within the `prompt_file` to retrieve the
                specific system prompt for `phyto_chat`.
                Defaults to `PROMPT_PATH`.
            api_key: API key for authentication with the Phyto model.
                Defaults to `API_KEY`.
            base_url: Base URL of the Phyto API service (`phyto_chat`).
                Defaults to `BASE_URL`.
            model: Identifier of the Phyto model to use via `phyto_chat`.
                Defaults to `MODEL_ID`.
            frequency_penalty: Penalty for token repetition (-2.0 to 2.0) for
                `phyto_chat`. Defaults to `FREQUENCY_PENALTY`.
            max_tokens: Maximum number of tokens to generate by `phyto_chat`.
                Defaults to `MAX_TOKENS`.
            n: Number of summary choices to generate by `phyto_chat`.
                Defaults to `N`.
            presence_penalty: Penalty for new tokens (-2.0 to 2.0) for
                `phyto_chat`. Defaults to `PRESENCE_PENALTY`.
            reasoning_effort: Specifies the reasoning effort for `phyto_chat`.
                Defaults to `REASONING_EFFORT`.
            response_format: Specifies the desired output format for
                `phyto_chat`. Defaults to `RESPONSE_FORMAT`.
            stream: Enable real-time token streaming output for `phyto_chat`.
                Defaults to `STREAM`.
            temperature: Controls randomness (0.0-1.0) for `phyto_chat`.
                Defaults to `TEMPERATURE`.
            top_p: Nucleus sampling threshold (0.0-1.0) for `phyto_chat`.
                Defaults to `TOP_P`.
            user: Unique session identifier for the end-user,
                passed to `phyto_chat`. Defaults to `USER`.
            timeout: Request timeout in seconds for all underlying asynchronous
                API calls (`gene_network`, `gene_symbol`, `gene_annotation`,
                `gene_retrieve`, `phyto_chat`). Defaults to `TIMEOUT`.
            retriable_codes: List of HTTP status codes that will trigger a
                retry for underlying API calls. Defaults to `RETRIABLE_CODES`.
            max_retries: Maximum number of retry attempts for underlying API
                calls. Defaults to `MAX_RETRIES`.
            max_concurrency: Maximum number of concurrent asynchronous
                operations (e.g., `gene_symbol`, `gene_annotation`,
                `gene_retrieve` calls) controlled by the internal semaphore.
                Defaults to `MAX_CONCURRENCY`.

        Returns:
            dict: The response dictionary from `phyto_chat`, which typically
            includes a 'choices' list with the generated summary of the gene's
            function. This dictionary is augmented with a 'doc_list' key
            (containing all documents retrieved for the primary gene and its
            network neighbors) and a 'total' key (a placeholder integer).
            Returns an empty dictionary if essential preliminary data (e.g.,
            symbols or documents for the primary gene) cannot be obtained.

        Raises:
            McpError: If any of the underlying asynchronous calls
                (`gene_network`, `gene_symbol`, `gene_annotation`,
                `gene_retrieve`, `phyto_chat`) fail after all retry attempts.
            KeyError: If a `species_code` (either the input `species_code` or
                one derived from `gene_network` results) is not found in the
                internal `SPECIES_CODE_MAP` when preparing data for
                `gene_retrieve` or the final prompt.
        """
        if use_analyst_agent:
            user_id = uuid1()
            output_dir = create_output_dir(user_id, gene_id)
            species_str = SPECIES_CODE_MAP[species_code].split('(')[1].strip(
                ')').lower()
            analysis_task = analysis_module(species=species_str,
                                            gene_id=gene_id,
                                            output=output_dir,
                                            user_id=user_id,
                                            batch=True)
            if not direct_return:
                analysis_task_str = json.dumps(analysis_task)
                manager.update_task(task_id, 'running',
                                    analysis_task_str, output_dir)

        species_gene_list = [(species_code, gene_id)]
        if use_data_agent:
            gene_network_results = await gene_network(
                species_code=species_code,
                gene_id=gene_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
                dialog_id=dialog_id,
                need_insight=need_insight,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            )
            gene_orthologs_list = gene_network_results[0]
            gene_paralogs_list = gene_network_results[1]
            gene_interaction_list = gene_network_results[2]
            species_gene_list += sum(gene_network_results, [])

        semaphore = asyncio.Semaphore(max_concurrency)
        tasks = [
            gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
                dialog_id=dialog_id,
                need_insight=need_insight,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in species_gene_list
        ] + [
            gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
                dialog_id=dialog_id,
                need_insight=need_insight,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in species_gene_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        gene_symbol_results = results[:len(species_gene_list)]
        gene_anno_results = results[len(species_gene_list):]
        species_gene_symbol_dict = {}
        for species_gene, gene_symbol_list in zip(
                species_gene_list, gene_symbol_results):
            if gene_symbol_list and gene_symbol_list is not McpError:
                species_gene_symbol_dict.update({
                    species_gene: gene_symbol_list})
        species_gene_anno_dict = {}
        for species_gene, gene_anno_dict in zip(
                species_gene_list, gene_anno_results):
            if gene_anno_dict and gene_anno_dict is not McpError:
                species_gene_anno_dict.update({species_gene: gene_anno_dict})

        gene_retrieve_results = await gene_retrieve(
            species=SPECIES_CODE_MAP[species_code],
            gene_symbol_list=species_gene_symbol_dict[(species_code, gene_id)],
            retrieve_url=retrieve_url,
            repo_id_dict=repo_id_dict,
            page_num=page_num,
            filter_string=filter_string,
            extra_repo_ids=extra_repo_ids,
            rerank_url=rerank_url,
            rerank_batch_size=rerank_batch_size,
            score_threshold=score_threshold,
            top_n=top_n,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
            semaphore=semaphore,
        )
        retrieve_results = []
        total_length = 0
        for file_id, eachdoc in enumerate(gene_retrieve_results['doc_list']):
            if eachdoc["subtitle"]:
                current_fragment = (
                    f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                    f'{eachdoc["subtitle"]}\n{eachdoc["content"]} '
                    f'[document {file_id+1} end]')
            else:
                current_fragment = (
                    f'[document {file_id+1} begin] {eachdoc["title"]}\n'
                    f'{eachdoc["content"]} [document {file_id+1} end]')
            if total_length + len(current_fragment) <= max_tokens:
                retrieve_results.append(current_fragment)
                total_length += len(current_fragment)
            else:
                break
        retrieve_results = '\n\n'.join(retrieve_results)

        if use_data_agent:
            orthologs_string = network_to_string(gene_orthologs_list,
                                                 species_gene_symbol_dict,
                                                 species_gene_anno_dict,
                                                 'Orthologous')
            paralogs_string = network_to_string(gene_paralogs_list,
                                                species_gene_symbol_dict,
                                                species_gene_anno_dict,
                                                'Paralogous')
            interaction_string = network_to_string(gene_interaction_list,
                                                   species_gene_symbol_dict,
                                                   species_gene_anno_dict,
                                                   'Potential interacting')
            gene_anno = species_gene_anno_dict[(species_code, gene_id)]
            phyto_response = await phyto_chat(
                user_query=get_prompt(
                    prompt_file,
                    'user/gene_function_network_anno',
                    {
                        'species': SPECIES_CODE_MAP[species_code],
                        'gene_string': '|'.join(species_gene_symbol_dict[
                            (species_code, gene_id)]),
                        'retrieve_results': retrieve_results,
                        'description_string': gene_anno['description'],
                        'go_string': '; '.join([go_list[1] for go_list
                                                in gene_anno['go']]),
                        'interpro_string': '; '.join([
                            ip_list[1] for ip_list in gene_anno['interpro']]),
                        'mapman_string': '; '.join([mm_list[1] for mm_list
                                                    in gene_anno['mapman']]),
                        'orthologs_string': orthologs_string,
                        'paralogs_string': paralogs_string,
                        'interaction_string': interaction_string,
                    },
                ),
                prompt_file=prompt_file,
                prompt_path=prompt_path,
                api_key=api_key,
                base_url=base_url,
                model=model,
                frequency_penalty=frequency_penalty,
                n=n,
                presence_penalty=presence_penalty,
                reasoning_effort=reasoning_effort,
                response_format=response_format,
                stream=stream,
                temperature=temperature,
                top_p=top_p,
                user=user,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            )
        else:
            phyto_response = await phyto_chat(
                user_query=get_prompt(
                    prompt_file,
                    'user/gene_function',
                    {
                        'species': SPECIES_CODE_MAP[species_code],
                        'gene_string': '|'.join(species_gene_symbol_dict[
                            (species_code, gene_id)]),
                        'retrieve_results': retrieve_results,
                    },
                ),
                prompt_file=prompt_file,
                prompt_path=prompt_path,
                api_key=api_key,
                base_url=base_url,
                model=model,
                frequency_penalty=frequency_penalty,
                n=n,
                presence_penalty=presence_penalty,
                reasoning_effort=reasoning_effort,
                response_format=response_format,
                stream=stream,
                temperature=temperature,
                top_p=top_p,
                user=user,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            )
        if direct_return:
            phyto_response['choices'][0]['message'].update(
                {'doc_list': gene_retrieve_results['doc_list'],
                 'total': 10000})
            return phyto_response
        _ = await update_task(
            url=update_task_url,
            server_id=task_id,
            server_status='finished',
            server_file_path='',
            tool_result=json.dumps(phyto_response),
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries)
        if use_analyst_agent:
            manager.update_task(task_id, 'finished',
                                analysis_task_str, output_dir)

    def run_async():
        asyncio.run(gene_function(
            species_code=species_code,
            gene_id=gene_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            dialog_id=dialog_id,
            need_insight=need_insight,
            retrieve_url=retrieve_url,
            repo_id_dict=repo_id_dict,
            page_num=page_num,
            filter_string=filter_string,
            extra_repo_ids=extra_repo_ids,
            rerank_url=rerank_url,
            rerank_batch_size=rerank_batch_size,
            score_threshold=score_threshold,
            top_n=top_n,
            prompt_file=prompt_file,
            prompt_path=prompt_path,
            api_key=api_key,
            base_url=base_url,
            model=model,
            frequency_penalty=frequency_penalty,
            max_tokens=max_tokens,
            n=n,
            presence_penalty=presence_penalty,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            user=user,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            use_data_agent=use_data_agent,
            use_analyst_agent=use_analyst_agent,
            direct_return=direct_return,
        ))

    if direct_return:
        return await gene_function(
            species_code=species_code,
            gene_id=gene_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            dialog_id=dialog_id,
            need_insight=need_insight,
            retrieve_url=retrieve_url,
            repo_id_dict=repo_id_dict,
            page_num=page_num,
            filter_string=filter_string,
            extra_repo_ids=extra_repo_ids,
            rerank_url=rerank_url,
            rerank_batch_size=rerank_batch_size,
            score_threshold=score_threshold,
            top_n=top_n,
            prompt_file=prompt_file,
            prompt_path=prompt_path,
            api_key=api_key,
            base_url=base_url,
            model=model,
            frequency_penalty=frequency_penalty,
            max_tokens=max_tokens,
            n=n,
            presence_penalty=presence_penalty,
            reasoning_effort=reasoning_effort,
            response_format=response_format,
            stream=stream,
            temperature=temperature,
            top_p=top_p,
            user=user,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
            max_concurrency=max_concurrency,
            use_data_agent=use_data_agent,
            use_analyst_agent=use_analyst_agent,
            direct_return=direct_return,
        )
    thread = Thread(target=run_async)
    thread.daemon = True
    thread.start()
    return task_id


async def get_interaction_gene_list(
    gene_id,
    database_url: str = dgc.DATABASE_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
    simplify_response: bool = dgc.SIMPLIFY_RESPONSE,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
) -> list:
    results = await nl2sql(
        'Give the query_gene_id_11, query_protein_11, interact_gene_id_11, '
        f"interact_protein_11 and combined_score_11 '{gene_id}' or "
        f"interact_gene_id_11 is '{gene_id}'.",
        database_url=database_url,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=simplify_response,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    gene_interaction_set = set()
    for gene_interaction in results['data']:
        if gene_interaction[0] == gene_id:
            gene_interaction_set.add((gene_interaction[2]))
        elif gene_interaction[2] == gene_id:
            gene_interaction_set.add((gene_interaction[0]))
    gene_interaction_list = sorted(gene_interaction_set)
    return gene_interaction_list


async def evolution_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/evolution_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'evolution_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'evolution_task')
    meta = get_prompt(prompt_file, 'user/evolution_analysis_meta')
    evo_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-evo-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='medium',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'evolution_task': evo_task}


async def protein_structure_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/structure_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'structure_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'protein_structure_task')
    meta = get_prompt(prompt_file, 'user/structure_analysis_meta')
    af3_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-structure-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='medium',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'protein_structure_task': af3_task}


async def promoter_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/promoter_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'promoter_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'promoter_task')
    meta = get_prompt(prompt_file, 'user/promoter_analysis_meta')
    promoter_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-motif-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'promoter_task': promoter_task}


async def protein_design_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/protein_design_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'protein_design_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'protein_design_task')
    meta = get_prompt(prompt_file, 'user/protein_design_analysis_meta')
    pr_design_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-prdesign-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='medium',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'protein_design_task': pr_design_task}


async def gene_expression_tissues(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/tissue',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['tissues']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'tissues_task')
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    tissues_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-tissues-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'tissues_task': tissues_task}


async def gene_expression_cultivars(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/cultivar',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['cultivars']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'cultivars_task')
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    cultivars_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-cultivars-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'cultivars_task': cultivars_task}


async def gene_expression_genotypes(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/genotype',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['genotypes']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'genotypes_task')
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    genotypes_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-genotypes-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'genotypes_task': genotypes_task}


async def gene_expression_treatments(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/treatment',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['treatments']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'treatments_task')
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    treatments_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-treatments-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'treatments_task': treatments_task}


async def single_cell_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    goal_description = get_prompt(prompt_file, 'user/single_cell_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'single_cell_analysis', species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'single_cell_task')
    meta = get_prompt(prompt_file, 'user/single_cell_analysis_meta')
    single_cell_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-singlecell-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'single_cell_task': single_cell_task}


async def ppi_analysis(
    species: str,
    gene_id: str,
    user_id: str = '',
    batch: bool = False,
    database_url: str = dgc.DATABASE_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
    simplify_response: bool = dgc.SIMPLIFY_RESPONSE,
    prompt_file: str = dgc.PROMPT_FILE,
    deepgenome_data: str = dgc.DEEPGENOME_DATA,
    output_dir: str = dgc.OUTPUT_DIR,
    model_url: str = sc.CODER_URL,
    model_name: str = sc.CODER_MODEL,
    coder_api_key: str = sc.CODER_API_KEY.get_secret_value(),
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    resource_dict: Dict[str, Dict[str, int]] = dgc.RESOURCE,
    app_id_dict: Dict[str, str] = dgc.APP_ID,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> dict:
    interaction_gene = await get_interaction_gene_list(
        gene_id=gene_id,
        database_url=database_url,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=simplify_response,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    if len(interaction_gene) == 0:
        return {'ppi_task': None}
    interaction_gene = ', '.join(interaction_gene)
    goal_description = get_prompt(
        prompt_file, 'user/ppi_analysis',
        {'gene_id': gene_id, 'interaction_gene': interaction_gene})
    data_list = get_data_list(deepgenome_data, 'ppi_analysis', species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(user_id, 'ppi_task')
    meta = get_prompt(prompt_file, 'user/ppi_analysis_meta')
    ppi_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        output_dir=output_dir,
        meta=meta,
        execute_code=True,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        task_name='deepgenome-agents-ppi-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='large',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'ppi_task': ppi_task}


async def smep_analysis(species,
                        gene_id,
                        epic_type='6mA',
                        output='/obs/phytomni/agent_data/test/',
                        user_id='',
                        batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'smep_task')
    data_list = get_data_list(dgc.DEEPGENOME_DATA, 'promoter_analysis', species)
    smep_goal = get_prompt(dgc.PROMPT_FILE, 'user/smep_analysis',
                           {'gene_id': gene_id, 'epic_type': epic_type})
    smep_meta = get_prompt(dgc.PROMPT_FILE, 'user/smep_analysis_meta')

    smep_task = await submit(
        goal_description=smep_goal,
        data_list=data_list,
        output_dir=output,
        meta=smep_meta,
        execute_code=True, 
        task_name="deepgenome-agents-smep-task", 
        compute_resource="small")

    task = {
        'smep_task': smep_task
    }

    return task


async def smoc_analysis(species,
                        gene_id,
                        output='/obs/phytomni/agent_data/test/',
                        user_id='',
                        batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'smoc_task')
    data_list = get_data_list(dgc.DEEPGENOME_DATA, 'promoter_analysis', species)
    # 染色质可及性预测写死了，预测的NIPCK
    smoc_goal = get_prompt(dgc.PROMPT_FILE, 'user/smoc_analysis',
                           {'gene_id': gene_id})
    smoc_meta = get_prompt(dgc.PROMPT_FILE, 'user/smoc_analysis_meta')

    smoc_task = await submit(
        goal_description=smoc_goal,
        data_list=data_list,
        output_dir=output,
        meta=smoc_meta,
        execute_code=True, 
        task_name="deepgenome-agents-smoc-task", 
        compute_resource="small")
    
    task = {
        'smoc_task': smoc_task
    }

    return task


async def test_api(species, 
                   gene_id, 
                   output='/obs/phytomni/agent_data/test/', 
                   user_id='', 
                   batch=False):
    species_code = 'osa'
    results = await nl2sql(f'List all the columns whose gene_id_1 is {gene_id} and species_code_1 is {species_code}?', 
                           simplify_response=False)
    result = pd.DataFrame(results['query_data'][1])
    msu_id = result[result.caption == 'msu_gene_id_1'].cell_value.values[0]
    if msu_id == None:
        task = {
            'tissues_task': None
        }

        return task
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'gene_expression_task')
    data_list = get_data_list(dgc.DEEPGENOME_DATA, 'gene_expression_analysis', species)
    tissues_data = data_list['tissues']
    tissue_goal = get_prompt(dgc.PROMPT_FILE, 'user/gene_expression_analysis/tissue', {'gene_id': msu_id})
    meta = get_prompt(dgc.PROMPT_FILE, 'user/gene_expression_analysis_meta')
    tissues_task = await submit(
        goal_description=tissue_goal,
        data_list=tissues_data,
        output_dir=output,
        meta=meta,
        execute_code=True, 
        task_name="deepgenome-agents-tissues-task-test", 
        compute_resource="small")

    task = {
        'tissues_task': tissues_task
    }

    return task


async def gene_expression_analysis(species,
                                   gene_id,
                                   output='/obs/phytomni/agent_data/test/',
                                   user_id='',
                                   batch=False):
    # TODO: implement gene_expression analysis
    '''
    conda activate af3 && pip install seaborn
    '''
    # Conversion gene ID
    # species_code 如何更改集成？！？
    species_code = 'osa'
    results = await nl2sql(f'List all the columns whose gene_id_1 is {gene_id} and species_code_1 is {species_code}?',
                           simplify_response=False)
    result = pd.DataFrame(results['query_data'][1])
    msu_id = result[result.caption == 'msu_gene_id_1'].cell_value.values[0]
    if msu_id == None:
        task = {
            'tissues_task': None,
            'cultivars_task': None,
            'genotypes_task': None,
            'treatments_task': None
        }

        return task
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'gene_expression_task')
    tissues_task = await gene_expression_tissues(species=species, 
                                                 gene_id=msu_id, 
                                                 output=output, 
                                                 user_id=user_id, 
                                                 batch=True)
    cultivars_task = await gene_expression_cultivars(species=species, 
                                                     gene_id=msu_id, 
                                                     output=output, 
                                                     user_id=user_id, 
                                                     batch=True)
    genotypes_task = await gene_expression_genotypes(species=species, 
                                                     gene_id=msu_id, 
                                                     output=output, 
                                                     user_id=user_id, 
                                                     batch=True)
    treatments_task = await gene_expression_treatments(species=species, 
                                                       gene_id=msu_id, 
                                                       output=output, 
                                                       user_id=user_id, 
                                                       batch=True)
    task = {}
    for d in [tissues_task, cultivars_task, genotypes_task, treatments_task]:
        task.update(d)

    return task


async def epic_analysis(species,
                        gene_id,
                        epic_type='6mA',
                        output='/obs/phytomni/agent_data/test/',
                        user_id='',
                        batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'epic_task')
    smep_task = await smep_analysis(species=species, 
                                    gene_id=gene_id, 
                                    epic_type=epic_type, 
                                    output=output, 
                                    user_id=user_id, 
                                    batch=True)
    smoc_task = await smoc_analysis(species=species, 
                                    gene_id=gene_id, 
                                    output=output, 
                                    user_id=user_id, 
                                    batch=True)
    task = {}
    for d in [smep_task, smoc_task]:
        task.update(d)

    return task


async def design_module(species,
                        gene_id,
                        output='/obs/phytomni/agent_data/test/',
                        user_id='',
                        batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'design_task')
    # step1: 蛋白质设计
    protein_design_task = await protein_design_analysis(species=species,
                                                        gene_id=gene_id,
                                                        output=output,
                                                        user_id=user_id,
                                                        batch=True)
    
    return protein_design_task


async def analysis_module(species,
                          gene_id,
                          output='/obs/phytomni/agent_data/test/',
                          user_id='',
                          batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'analysis_task')
    # step1: 进化分析
    evo_task = await evolution_analysis(species=species,
                                        gene_id=gene_id,
                                        output=output,
                                        user_id=user_id,
                                        batch=True)
    print(evo_task)
    # step2: 启动子分析
    promoter_task = await promoter_analysis(species=species,
                                            gene_id=gene_id,
                                            output=output,
                                            user_id=user_id,
                                            batch=True)
    print(promoter_task)
    # step3: 表观修饰分析
    epic_task = await epic_analysis(species=species,
                                    gene_id=gene_id,
                                    epic_type='6mA',
                                    output=output,
                                    user_id=user_id,
                                    batch=True)
    print(epic_task)
    # step4: 表达模式分析
    gene_exp_task = await gene_expression_analysis(species=species,
                                                   gene_id=gene_id,
                                                   output=output,
                                                   user_id=user_id,
                                                   batch=True)
    print(gene_exp_task)
    # step5: 单细胞表达模式分析
    single_cell_exp_task = await single_cell_analysis(species=species, 
                                                      gene_id=gene_id, 
                                                      output=output, 
                                                      user_id=user_id, 
                                                      batch=True)
    print(single_cell_exp_task)
    # step6: 蛋白质分析
    # function_task = await protein_function_analysis(species=species,
    #                                                 gene_id=gene_id,
    #                                                 output=output,
    #                                                 user_id=user_id,
    #                                                 batch=True)
    # print(function_task)
    structure_task = await protein_structure_analysis(species=species,
                                                      gene_id=gene_id,
                                                      output=output,
                                                      user_id=user_id,
                                                      batch=True)
    print(structure_task)
    ppi_task = await ppi_analysis(species=species,
                                  gene_id=gene_id,
                                  output=output,
                                  user_id=user_id,
                                  batch=True)
    print(ppi_task)
    
    analysis_task = {}
    # for d in [evo_task, promoter_task, epic_task, gene_exp_task, single_cell_exp_task, function_task, structure_task, ppi_task]:
    for d in [evo_task, promoter_task, epic_task, gene_exp_task, single_cell_exp_task, structure_task, ppi_task]:
        analysis_task.update(d)

    return analysis_task


async def gene_analysis(species,
                        gene_id,
                        output='/obs/phytomni/agent_data/test/',
                        user_id='',
                        batch=False):
    if not batch:
        if user_id == '':
            user_id = uuid1()
        output = create_output_dir(user_id, 'analysis_task')
    # step1: evolution analysis
    # evo_task = await evolution_analysis(species=species,
    #                                     gene_id=gene_id,
    #                                     output=output,
    #                                     user_id=user_id,
    #                                     batch=False)
    # step2: promoter motif analysis
    # promoter_task = await promoter_analysis(species=species,
    #                                         gene_id=gene_id,
    #                                         output=output,
    #                                         user_id=user_id,
    #                                         batch=False)
    # step3: epigentics analysis
    epic_task = await epic_analysis(species=species,
                                    gene_id=gene_id,
                                    epic_type='6mA',
                                    output=output,
                                    user_id=user_id,
                                    batch=False)
    # step4: bulk gene expression analysis
    # gene_exp_task = await gene_expression_analysis(species=species,
    #                                                gene_id=gene_id,
    #                                                output=output,
    #                                                user_id=user_id,
    #                                                batch=False)
    # step5: single cell gene expression analysis
    single_cell_exp_task = await single_cell_analysis(species=species, 
                                                      gene_id=gene_id, 
                                                      output=output, 
                                                      user_id=user_id, 
                                                      batch=False)
    # step6: 蛋白质分析
    # function_task = await protein_function_analysis(species=species,
    #                                                 gene_id=gene_id,
    #                                                 output=output,
    #                                                 user_id=user_id,
    #                                                 batch=True)
    # print(function_task)
    # structure_task = await protein_structure_analysis(species=species,
    #                                                   gene_id=gene_id,
    #                                                   output=output,
    #                                                   user_id=user_id,
    #                                                   batch=False)
    
    analysis_task = {}
    for d in [epic_task, single_cell_exp_task]:
    # for d in [evo_task, promoter_task, epic_task, gene_exp_task, single_cell_exp_task, structure_task]:
        analysis_task.update(d)

    return analysis_task


async def generate_gene_summary():
    pass


async def generate_analysis_results():
    species = "oryza sativa"
    gene_id = "Os01g0177400"
    user_id = "test_user"
    
    gene_task = await gene_analysis(species=species, 
                                    gene_id=gene_id, 
                                    user_id=user_id, 
                                    batch=True)
    print(gene_task)
    processing_wait_tasks = []
    for task_name, task_dict in gene_task.items():
        processing_wait_tasks.append(
            wait_and_download(
                task_name=task_name, 
                task_dict=task_dict, 
                gene_id=gene_id
            )
        )
    results = await asyncio.gather(*processing_wait_tasks)

    return "All Job Finish."


async def wait_and_download(
        task_name: str, 
        task_dict: str, 
        gene_id: str
):
    target_map = {
        'smep_task': ['.out', '.summary'], 
        'smoc_task': ['.csv', '.summary'], 
        'evolution_task': ['.txt', 'domain', '.nwk', '.newick', '.summary'], 
        'structure_task': ['.cif', '.json', '.summary'], 
        'promoter_task': ['meme.txt', '.eps', '.html', '.summary'], 
        'single_cell_task': ['.png', '.summary'], 
        'tissues_task': ['.png', '.summary'], 
        'cultivars_task': ['.png', '.summary'], 
        'genotypes_task': ['.png', '.summary'], 
        'treatments_task': ['.png', '.summary']
    }
    output_dir = task_dict['output_dir'].split("/obs/phytomni/")[-1]
    wait_info = await wait_for_completion(task_dict['task_id'])

    for download_status in download_obs_out(gene_id=gene_id, 
                                            obs_output_path=output_dir, 
                                            is_all=False, 
                                            target_file_feature=target_map[task_name]):
        print(download_status)
    
    return f"{task_name} results download succeed."


def download_obs_out(
    gene_id: str, 
    obs_output_path: str,
    output_dir: str = dgc.DEEPGENOME_OUT, 
    is_all: bool = True, 
    target_file_feature: str = "", 
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME, 
):
    output_path = f"{output_dir}/{gene_id}"
    Path(output_path).mkdir(parents=True, exist_ok=True)
    headers = GetObjectHeader()
    headers.if_modified_since = 'date'

    obsclient = ObsClient(access_key_id=access_key_id,
                          secret_access_key=secret_access_key,
                          server=obs_server)
    max_num = 1000
    mark = None
    try:
        while True:
            file_response = obsclient.listObjects(bucket_name, 
                                                  obs_output_path, 
                                                  marker=mark, 
                                                  max_keys=max_num, 
                                                  encoding_type='url')
            if file_response.status < 300: 
                for content in file_response.body.contents: 
                    obj_file = content.key
                    # skip folder
                    if obj_file.endswith('/'):
                        continue
                    output_file = obj_file.split('/')[-1]
                    # 如果非全部下载时，需要匹配文件特征，如“.png”等
                    # if not is_all and target_file_feature not in output_file:
                    if not is_all and not any(
                        output_file.endswith(suffix) for suffix in target_file_feature
                    ):
                        
                        continue
                    full_path = f"{output_path}/{output_file}"
                    download_response = obsclient.getObject(bucket_name, 
                                                            obj_file, 
                                                            full_path, 
                                                            headers=headers)
                    if download_response.status > 300:
                        yield f"{output_file} download failed."
                        continue
                    else:
                        yield f"{output_file} download succeed."
                        continue
                if file_response.body.is_truncated is True:
                    mark = file_response.body.next_marker
                else:
                    break
            else: 
                raise OSError(f'Get File List Failed\nrequestId: {file_response.requestId}\n'
                              f'errorCode: {file_response.errorCode}\n'
                              f'errorMessage: {file_response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Download File Failed\n{format_exc()}') from exc


def create_output_dir(
    user_id: str,
    task: str,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
) -> str:
    obsclient = ObsClient(access_key_id=access_key_id,
                          secret_access_key=secret_access_key,
                          server=obs_server)
    try:
        output_dir = (f'agent_data/user_data/{user_id}/output/'
                      f'{task}_{int(time.time())}_{uuid1()}/')
        response = obsclient.putContent(bucketName=bucket_name,
                                        objectKey=output_dir,
                                        content=None)
        if response.status < 300:
            return f'/obs/{bucket_name}/{output_dir}'
        raise OSError(f'Put File Failed\nrequestId: {response.requestId}\n'
                      f'errorCode: {response.errorCode}\n'
                      f'errorMessage: {response.errorMessage}')
    except Exception as exc:
        raise OSError(f'Put File Failed\n{format_exc()}') from exc


def get_data_list(data_file: str,
                  analysis_type: str,
                  species: str) -> list:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        data_file: data_list_file for json format
        analysis_type: analysis_type[evolution_analysis, deepgo2_analysis,
                                     structure_analysis, prompter_analysis,
                                     protein_design_analysis,
                                     gene_expression_analysis, ppi_analysis]
        species: 65 species ...

    Returns:
        data_list for analysis
    """
    try:
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f'Data file not found: {data_file}') from exc
    try:
        analysis_data_list = data[analysis_type]
    except KeyError as exc:
        raise KeyError(f'Analysis type not found: {analysis_type}') from exc
    try:
        data_list = analysis_data_list[species]
    except KeyError as exc:
        raise KeyError(f'Species not found: {species}') from exc
    return data_list
