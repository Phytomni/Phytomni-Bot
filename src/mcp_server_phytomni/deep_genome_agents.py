# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: maoyc_0316@163.com
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides a suite of asynchronous functions for analyzing gene
function and related biological processes in plant genomes.

Key functionalities include:
- Retrieving gene network information (orthologs, paralogs, interactions).
- Fetching gene symbols and annotations (description, GO terms, InterPro,
  MapMan).
- Summarizing gene networks into human-readable strings.
- Retrieving documents related to gene symbols from literature repositories.
- Performing a comprehensive gene function analysis by orchestrating the
  above operations and using a large language model to generate a summary.
- Submitting and monitoring various types of specialized gene analysis tasks
  (e.g., evolution analysis, protein structure prediction,
   expression analysis).
- Downloading and summarizing results from completed analysis tasks.
"""
import asyncio
from collections import deque
from json import dumps, loads
from pathlib import Path
from random import randint
from threading import Thread
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid1

from mcp.shared.exceptions import McpError

from .analyst_agents import create_output_dir, download_obs_out, get_data_list
from .analyst_agents import submit, wait_for_completion
from .chat_agents import phyto_chat
from .config.defaults import DeepGenomeConfig
from .config.settings import SensitiveConfig
from .data_agents import nl2sql
from .knowledge_agents import multi_retrieve, response_to_string
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
            to the `nl2sql` function.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls.

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
    message_content = ('Give the query_gene_id_11, query_protein_11, '
                       'interact_gene_id_11 and interact_protein_11 where '
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
            to the `nl2sql` function.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `nl2sql` calls if this function is called
            multiple times concurrently.

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
            to the `nl2sql` function.
        subject_id: Identifier for the specific database subject or schema to
            query against, passed to the `nl2sql` function.
        dialog_id: Identifier for the current dialog or conversation session,
            passed to the `nl2sql` function.
        need_insight: Flag indicating whether to generate insights based on the
            query results, passed to the `nl2sql` function.
        timeout: Request timeout in seconds for the underlying `nl2sql` API
            calls.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `nl2sql` API calls.
        max_retries: Maximum number of retry attempts for underlying `nl2sql`
            API calls.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `nl2sql` calls if this function is called
            multiple times concurrently.

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
                               the summary.

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
        retrieve_url: The URL of the retrieval service.
        repo_id_dict: A dictionary mapping repository IDs (str) to their
            respective page sizes (int) for document retrieval, passed to
            `multi_retrieve`.
        page_num: Pagination page number for retrieval results from each
            repository, passed to `multi_retrieve`.
        filter_string: Optional filter criteria string for metadata filtering
            during document retrieval, passed to `multi_retrieve`.
        extra_repo_ids: Optional list of additional repository IDs to include
            in the document retrieval, passed to `multi_retrieve`.
        score_threshold: Minimum relevance score threshold applied during
            document retrieval by `multi_retrieve`.
        top_n: The number of top-scoring documents to retrieve from each
            individual `multi_retrieve` call, and also the number of
            top-scoring documents to return in the final merged and sorted
            list.
        timeout: Request timeout in seconds for the underlying `multi_retrieve`
            API calls.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking operations, if
            reranking is applied to retrieved documents.
        retriable_codes: List of HTTP status codes that will trigger a retry
            for underlying `multi_retrieve` API calls.
        max_retries: Maximum number of retry attempts for underlying
            `multi_retrieve` API calls.
        semaphore: An optional `asyncio.Semaphore` instance to limit the
            concurrency of `multi_retrieve` calls if this function is called
            multiple times concurrently.

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
                'doc_list': sorted_docs,
                'total': 10000,
            }
        return {}

    if semaphore is not None:
        async with semaphore:
            return await get_gene_retrieve()
    else:
        return await get_gene_retrieve()


async def gene_function(
    species_code: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = dgc.BATCH,
    epic_type: str = dgc.EPIC_TYPE,
    create_task_url: str = dgc.CREATE_TASK_URL,
    update_task_url: str = dgc.UPDATE_TASK_URL,
    database_url: str = dgc.DATABASE_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
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
    retrieve_url: str = dgc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = dgc.REPO_ID_DICT,
    page_num: int = dgc.PAGE_NUM,
    filter_string: Optional[str] = dgc.FILTER_STRING,
    extra_repo_ids: Optional[List[str]] = dgc.EXTRA_REPO_IDS,
    rerank_url: str = dgc.RERANK_URL,
    rerank_batch_size: int = dgc.RERANK_BATCH_SIZE,
    score_threshold: float = dgc.SCORE_THRESHOLD,
    top_n: int = dgc.TOP_N,
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
    deepgenome_out: str = dgc.DEEPGENOME_OUT,
    result_template: str = dgc.TEMPLATE,
    download_path: str = dgc.DOWNLOAD_PATH,
    marker: Optional[str] = dgc.DOWNLOAD_MARKER,
    max_keys: int = dgc.DOWNLOAD_MAX_KEYS,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_concurrency: int = dgc.MAX_CONCURRENCY,
    max_poll: float = dgc.MAX_POLL,
    use_data_agent: bool = True,
    use_analyst_agent: bool = True,
    direct_return: bool = False,
) -> Union[Dict[str, Any], str]:
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
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        epic_type: The type of EPIC analysis to perform.
        create_task_url: The URL for creating a task on the server.
        update_task_url: The URL for updating a task on the server.
        database_url: The URL for the database service.
        workspace_id: The workspace identifier.
        subject_id: The database subject identifier.
        dialog_id: The dialog identifier.
        need_insight: Flag indicating whether insights should be generated.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        retrieve_url: The URL for the retrieval service.
        repo_id_dict: A dictionary of repository identifiers.
        page_num: The page number for retrieval.
        filter_string: A string to filter retrieval results.
        extra_repo_ids: A list of extra repository identifiers.
        rerank_url: The URL for the rerank service.
        rerank_batch_size: The batch size for reranking.
        score_threshold: The minimum score threshold for retrieval.
        top_n: The number of top results to return.
        prompt_path: The path to the prompt.
        api_key: The API key for the language model.
        base_url: The base URL for the language model.
        frequency_penalty: The frequency penalty for the language model.
        max_tokens: The maximum number of tokens to generate.
        n: The number of completions to generate.
        presence_penalty: The presence penalty for the language model.
        reasoning_effort: The reasoning effort for the language model.
        response_format: The format for the language model response.
        stream: Flag indicating whether to stream the response.
        temperature: The temperature for the language model.
        top_p: The top_p value for the language model.
        user: The user for the language model.
        deepgenome_out: The output directory for deep genome results.
        result_template: The path to the result template.
        download_path: The path to download files.
        marker: The marker for downloading files.
        max_keys: The maximum number of keys to download.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_concurrency: Maximum number of concurrent operations.
        max_poll: Maximum duration in seconds to monitor the task.
        use_data_agent: Flag indicating whether to use the data agent.
        use_analyst_agent: Flag indicating whether to use the analyst agent.
        direct_return: If True, awaits the full pipeline and returns the
            result dictionary. If False, starts the pipeline in a background
            thread and immediately returns a server_task_id string.

    Returns:
        Union[Dict[str, Any], str]: If `direct_return` is True, returns the
        response dictionary from `phyto_chat`, augmented with 'doc_list' and
        'total'. If `direct_return` is False, returns the `server_task_id`
        string for tracking the background task.

    Raises:
        McpError: If any of the underlying asynchronous calls (`gene_network`,
            `gene_symbol`, `gene_annotation`, `gene_retrieve`, `phyto_chat`)
            fail after all retry attempts.
        KeyError: If a `species_code` is not found in `SPECIES_CODE_MAP`.
        ValueError: If `use_analyst_agent` and `direct_return` are both True.
    """
    if use_analyst_agent and direct_return:
        raise ValueError(
            'When use_analyst_agent is True, direct_return cannot be True, '
            'because the analyst agent is time-consuming and cannot return '
            'directly.'
        )

    async def _execute_pipeline(server_task_id: str) -> Dict[str, Any]:
        """The core logic of the gene function analysis pipeline."""
        nonlocal output_dir, user_id

        if use_analyst_agent:
            user_id = uuid1()
            output_dir = create_output_dir(
                user_id=user_id,
                task=gene_id,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=obs_server,
                bucket_name=bucket_name,
            )
            species_str = SPECIES_CODE_MAP[species_code].split('(')[1].strip(
                ')').lower()
            gene_task = await submit_gene_analysis(
                species=species_str,
                gene_id=gene_id,
                epic_type=epic_type,
                user_id=user_id,
                batch=batch,
                database_url=database_url,
                workspace_id=workspace_id,
                subject_id=subject_id,
                dialog_id=dialog_id,
                need_insight=need_insight,
                prompt_file=prompt_file,
                deepgenome_data=deepgenome_data,
                output_dir=output_dir,
                model_url=model_url,
                model_name=model_name,
                coder_api_key=coder_api_key,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=obs_server,
                bucket_name=bucket_name,
                analysis_url=analysis_url,
                region=region,
                resource_dict=resource_dict,
                app_id_dict=app_id_dict,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
                max_poll=max_poll,
            )
            gene_task_str = dumps(gene_task)
            manager.update_task(server_task_id, 'running',
                                gene_task_str, output_dir)

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
        symbol_tasks = [
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
            ) for each_species_code, each_gene_id in species_gene_list
        ]
        anno_tasks = [
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
            ) for each_species_code, each_gene_id in species_gene_list
        ]

        results = await asyncio.gather(*(symbol_tasks + anno_tasks),
                                       return_exceptions=True)
        gene_symbol_results = results[:len(species_gene_list)]
        gene_anno_results = results[len(species_gene_list):]

        species_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(species_gene_list,
                                         gene_symbol_results)
            if res and not isinstance(res, McpError)
        }
        species_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(species_gene_list, gene_anno_results)
            if res and not isinstance(res, McpError)
        }

        gene_retrieve_results = await gene_retrieve(
            species=SPECIES_CODE_MAP[species_code],
            gene_symbol_list=species_gene_symbol_dict.get(
                (species_code, gene_id), []),
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
        for i, doc in enumerate(gene_retrieve_results.get('doc_list', [])):
            header = f"[document {i+1} begin] {doc['title']}"
            body = (f"{doc['subtitle']}\n{doc['content']}"
                    if doc.get('subtitle') else doc.get('content', ''))
            fragment = f'{header}\n{body} [document {i+1} end]'
            if total_length + len(fragment) <= max_tokens:
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break
        retrieve_context = '\n\n'.join(retrieve_results)

        if use_data_agent:
            orthologs_string = network_to_string(
                gene_orthologs_list, species_gene_symbol_dict,
                species_gene_anno_dict, 'Orthologous')
            paralogs_string = network_to_string(
                gene_paralogs_list, species_gene_symbol_dict,
                species_gene_anno_dict, 'Paralogous')
            interaction_string = network_to_string(
                gene_interaction_list, species_gene_symbol_dict,
                species_gene_anno_dict, 'Potential interacting')
            gene_anno = species_gene_anno_dict.get((species_code, gene_id), {})
            prompt_vars = {
                'species': SPECIES_CODE_MAP[species_code],
                'gene_string': '|'.join(species_gene_symbol_dict.get(
                    (species_code, gene_id), [])),
                'retrieve_results': retrieve_context,
                'description_string': gene_anno.get('description', ''),
                'go_string': '; '.join(
                    go[1] for go in gene_anno.get('go', [])),
                'interpro_string': '; '.join(ip[1] for ip in gene_anno.get(
                    'interpro', [])),
                'mapman_string': '; '.join(mm[1] for mm in gene_anno.get(
                    'mapman', [])),
                'orthologs_string': orthologs_string,
                'paralogs_string': paralogs_string,
                'interaction_string': interaction_string,
            }
            user_query = get_prompt(
                prompt_file, 'user/gene_function_network_anno', prompt_vars)
        else:
            prompt_vars = {
                'species': SPECIES_CODE_MAP[species_code],
                'gene_string': '|'.join(species_gene_symbol_dict.get(
                    (species_code, gene_id), [])),
                'retrieve_results': retrieve_context,
            }
            user_query = get_prompt(
                prompt_file, 'user/gene_function', prompt_vars)

        phyto_response = await phyto_chat(
            user_query=user_query,
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
            stream=stream, temperature=temperature,
            top_p=top_p,
            user=user,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        )

        phyto_response['choices'][0]['message'].update({
            'doc_list': gene_retrieve_results.get('doc_list', []),
            'total': 10000
        })
        phyto_str = response_to_string(phyto_response)

        if use_analyst_agent:
            server_file_path = await summarize_gene_analysis(
                gene_id=gene_id,
                gene_task=gene_task,
                deepgenome_out=deepgenome_out,
                result_template=result_template,
                analysis_url=analysis_url,
                region=region,
                download_path=download_path,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=obs_server,
                bucket_name=bucket_name,
                marker=marker,
                max_keys=max_keys,
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
                max_poll=max_poll,
            )
            prepend_to_file(
                server_file_path,
                f'# In-depth Gene Characterization of {gene_id}\n'
                f'{phyto_str}\n')
            with open(server_file_path, 'r', encoding='utf-8') as f:
                phyto_response['choices'][0]['message']['content'] = f.read()

            await update_task(
                url=update_task_url,
                server_id=server_task_id,
                server_status='finished',
                server_file_path=server_file_path,
                tool_result=dumps(phyto_response),
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            )
            manager.update_task(server_task_id, 'finished',
                                gene_task_str, output_dir)
        else:
            deepgenome_path = Path(deepgenome_out)
            deepgenome_path.mkdir(parents=True, exist_ok=True)
            server_file_path = str(deepgenome_path / f'{gene_id}_results.md')
            with open(server_file_path, 'w', encoding='utf-8') as f:
                f.write(f'# In-depth Gene Characterization of {gene_id}\n'
                        + phyto_str)

            await update_task(
                url=update_task_url,
                server_id=server_task_id,
                server_status='finished',
                server_file_path=server_file_path,
                tool_result=dumps(phyto_response),
                timeout=timeout,
                retriable_codes=retriable_codes,
                max_retries=max_retries,
            )
            manager.update_task(server_task_id, 'finished', '', '')

        return phyto_response

    manager = _get_manager()
    server_task_id = manager.create_task()
    await create_task(
        url=create_task_url,
        server_id=server_task_id,
        server_status='running',
        tool_name='DeepGenomeAgent',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )

    if direct_return:
        return await _execute_pipeline(server_task_id)

    def thread_target():
        """Function to run the async pipeline in a new event loop."""
        asyncio.run(_execute_pipeline(server_task_id))

    thread = Thread(target=thread_target)
    thread.daemon = True
    thread.start()
    return {'task_id': server_task_id}


async def get_interaction_gene_list(
    gene_id: str,
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
    """Get a list of genes that interact with the given gene.

    This function queries the database to find genes that interact with the
    provided gene ID. It returns a list of interacting gene IDs.

    Args:
        gene_id: The identifier of the gene for which to find interactors.
        database_url: The URL for the database service.
        workspace_id: The workspace identifier.
        subject_id: The database subject identifier.
        dialog_id: The dialog identifier.
        need_insight: Flag indicating whether insights should be generated.
        simplify_response: Flag indicating whether to simplify the response.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.

    Returns:
        A list of gene IDs that interact with the given gene.
    """
    response = await nl2sql(
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
    for gene_interaction in response['data']:
        if gene_interaction[0] == gene_id:
            gene_interaction_set.add((gene_interaction[2]))
        elif gene_interaction[2] == gene_id:
            gene_interaction_set.add((gene_interaction[0]))
    gene_interaction_list = sorted(gene_interaction_set)
    return gene_interaction_list


async def evolution_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
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
    """Perform an evolutionary analysis of a gene.

    This function submits a task to perform an evolutionary analysis of the
    given gene. It constructs a goal description and data list, and then
    calls the `submit` function to initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the evolutionary
        analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/evolution_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'evolution_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='evolution_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/evolution_analysis_meta')
    evo_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a protein structure analysis of a gene.

    This function submits a task to perform a protein structure analysis of the
    given gene. It constructs a goal description and data list, and then
    calls the `submit` function to initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the protein structure
        analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/structure_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'structure_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='protein_structure_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/structure_analysis_meta')
    af3_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a promoter analysis of a gene.

    This function submits a task to perform a promoter analysis of the
    given gene. It constructs a goal description and data list, and then
    calls the `submit` function to initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the promoter analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/promoter_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'promoter_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='promoter_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/promoter_analysis_meta')
    promoter_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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


async def gene_expression_tissues(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
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
    """Perform a gene expression analysis across different tissues.

    This function submits a task to perform a gene expression analysis across
    different tissues for the given gene. It constructs a goal description
    and data list, and then calls the `submit` function to initiate the
    analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the tissue expression
        analysis.
    """
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/tissue',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['tissues']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='tissues_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    tissues_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a gene expression analysis across different cultivars.

    This function submits a task to perform a gene expression analysis across
    different cultivars for the given gene. It constructs a goal description
    and data list, and then calls the `submit` function to initiate the
    analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the cultivar
        expression analysis.
    """
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/cultivar',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['cultivars']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='cultivars_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    cultivars_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a gene expression analysis across different genotypes.

    This function submits a task to perform a gene expression analysis across
    different genotypes for the given gene. It constructs a goal description
    and data list, and then calls the `submit` function to initiate the
    analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the genotype
        expression analysis.
    """
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/genotype',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['genotypes']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='genotypes_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    genotypes_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a gene expression analysis across different treatments.

    This function submits a task to perform a gene expression analysis across
    different treatments for the given gene. It constructs a goal description
    and data list, and then calls the `submit` function to initiate the
    analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the treatment
        expression analysis.
    """
    goal_description = get_prompt(
        prompt_file, 'user/gene_expression_analysis/treatment',
        {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'gene_expression_analysis',
                              species)['treatments']
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='treatments_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/gene_expression_analysis_meta')
    treatments_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
    user_id: str = dgc.USER_ID,
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
    """Perform a single-cell expression analysis of a gene.

    This function submits a task to perform a single-cell expression analysis
    of the given gene. It constructs a goal description and data list, and then
    calls the `submit` function to initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the single-cell
        analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/single_cell_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'single_cell_analysis', species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='single_cell_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/single_cell_analysis_meta')
    single_cell_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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


async def smep_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = False,
    epic_type: str = dgc.EPIC_TYPE,
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
    """Perform an SMEP analysis of a gene.

    This function submits a task to perform an SMEP (Single-Molecule
    Epigenomics Profiling) analysis of the given gene. It constructs a goal
    description and data list, and then calls the `submit` function to
    initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        epic_type: The type of EPIC analysis to perform.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the SMEP analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/smep_analysis',
                                  {'gene_id': gene_id, 'epic_type': epic_type})
    data_list = get_data_list(deepgenome_data, 'promoter_analysis',
                              species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='smep_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/smep_analysis_meta')
    smep_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
        task_name='deepgenome-agents-smep-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'smep_task': smep_task}


async def smoc_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
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
    """Perform an SMOC analysis of a gene.

    This function submits a task to perform an SMOC (Single-Molecule Omics
    Clustering) analysis of the given gene. It constructs a goal description
    and data list, and then calls the `submit` function to initiate the
    analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the SMOC analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/smoc_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'promoter_analysis', species)
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='smoc_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/smoc_analysis_meta')
    smoc_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
        task_name='deepgenome-agents-smoc-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'smoc_task': smoc_task}


async def haplotypes_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = dgc.BATCH,
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
    """Perform a haplotype analysis of a gene.

    This function submits a task to perform a haplotype analysis of the
    given gene. It constructs a goal description and data list, and then
    calls the `submit` function to initiate the analysis.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for the haplotype
        analysis.
    """
    goal_description = get_prompt(prompt_file, 'user/haplotypes_analysis',
                                  {'gene_id': gene_id})
    data_list = get_data_list(deepgenome_data, 'haplotypes_analysis', species)
    data_list = loads(dumps(data_list).replace('gene_id', gene_id))
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='haplotypes_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    meta = get_prompt(prompt_file, 'user/haplotypes_analysis_meta')
    haplotypes_task = await submit(
        goal_description=goal_description,
        data_list=data_list,
        user_id=user_id,
        is_create_dir=False,
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
        task_name='deepgenome-agents-haplotypes-task',
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        compute_resource='small',
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {'haplotypes_task': haplotypes_task}


async def gene_expression_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = dgc.BATCH,
    database_url: str = dgc.DATABASE_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
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
    """Perform a comprehensive gene expression analysis.

    This function orchestrates a series of gene expression analyses across
    tissues, cultivars, genotypes, and treatments. It first retrieves the MSU
    gene ID from the database, and then calls the respective analysis functions
    for each category.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        database_url: The URL for the database service.
        workspace_id: The workspace identifier.
        subject_id: The database subject identifier.
        dialog_id: The dialog identifier.
        need_insight: Flag indicating whether insights should be generated.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for all expression
        analyses.
    """
    response = await nl2sql(
        f'List all the columns whose gene_id_1 is {gene_id} and '
        f'species_code_1 is {find_species_code(species)}?',
        database_url=database_url,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        simplify_response=False,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    msu_id = next(item['cell_value'] for item in response['query_data'][1]
                  if item['caption'] == 'msu_gene_id_1')
    if not msu_id:
        return {'tissues_task': None,
                'cultivars_task': None,
                'genotypes_task': None,
                'treatments_task': None}
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='gene_expression_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    tissues_task = await gene_expression_tissues(
        species=species,
        gene_id=msu_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    cultivars_task = await gene_expression_cultivars(
        species=species,
        gene_id=msu_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    genotypes_task = await gene_expression_genotypes(
        species=species,
        gene_id=msu_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    treatments_task = await gene_expression_treatments(
        species=species,
        gene_id=msu_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {**tissues_task, **cultivars_task,
            **genotypes_task, **treatments_task}


async def epic_analysis(
    species: str,
    gene_id: str,
    user_id: str = dgc.USER_ID,
    batch: bool = dgc.BATCH,
    epic_type: str = dgc.EPIC_TYPE,
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
    """Perform an EPIC analysis of a gene.

    This function orchestrates an SMEP and SMOC analysis of the given gene.
    It calls the respective analysis functions and returns the combined task
    information.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        epic_type: The type of EPIC analysis to perform.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for both SMEP and SMOC
        analyses.
    """
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='epic_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    smep_task = await smep_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        epic_type=epic_type,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    smoc_task = await smoc_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {**smep_task, **smoc_task}


async def submit_gene_analysis(
    species: str,
    gene_id: str,
    epic_type: str = dgc.EPIC_TYPE,
    user_id: str = dgc.USER_ID,
    batch: bool = dgc.BATCH,
    database_url: str = dgc.DATABASE_URL,
    workspace_id: str = dgc.WORKSPACE_ID,
    subject_id: str = dgc.SUBJECT_ID,
    dialog_id: str = dgc.DIALOG_ID,
    need_insight: bool = dgc.NEED_INSIGHT,
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
    """Submit a comprehensive gene analysis pipeline.

    This function orchestrates a complete gene analysis pipeline, including
    evolutionary analysis, promoter analysis, EPIC analysis, gene expression
    analysis, single-cell analysis, and protein structure analysis. It calls
    the respective analysis functions and returns the combined task
    information.

    Args:
        species: The species of the gene.
        gene_id: The identifier of the gene to analyze.
        epic_type: The type of EPIC analysis to perform.
        user_id: The user identifier for this task.
        batch: Flag indicating whether the operation is part of a batch.
        database_url: The URL for the database service.
        workspace_id: The workspace identifier.
        subject_id: The database subject identifier.
        dialog_id: The dialog identifier.
        need_insight: Flag indicating whether insights should be generated.
        prompt_file: The path to the prompt file.
        deepgenome_data: The path to the deep genome data.
        output_dir: The directory to store output files.
        model_url: The URL for the model service.
        model_name: The name of the model.
        coder_api_key: The API key for the coder service.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        resource_dict: A dictionary of resource configurations.
        app_id_dict: A dictionary of application identifiers.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        A dictionary containing the task information for all analyses in the
        pipeline.
    """
    if not batch:
        if not user_id:
            user_id = uuid1()
        output_dir = create_output_dir(
            user_id=user_id,
            task='analysis_task',
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            bucket_name=bucket_name,
        )
    evo_task = await evolution_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    promoter_task = await promoter_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    epic_task = await epic_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        epic_type=epic_type,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    gene_exp_task = await gene_expression_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        database_url=database_url,
        workspace_id=workspace_id,
        subject_id=subject_id,
        dialog_id=dialog_id,
        need_insight=need_insight,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    single_cell_exp_task = await single_cell_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    structure_task = await protein_structure_analysis(
        species=species,
        gene_id=gene_id,
        user_id=user_id,
        batch=batch,
        prompt_file=prompt_file,
        deepgenome_data=deepgenome_data,
        output_dir=output_dir,
        model_url=model_url,
        model_name=model_name,
        coder_api_key=coder_api_key,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        obs_server=obs_server,
        bucket_name=bucket_name,
        analysis_url=analysis_url,
        region=region,
        resource_dict=resource_dict,
        app_id_dict=app_id_dict,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
        max_poll=max_poll,
    )
    return {**evo_task, **promoter_task, **epic_task, **gene_exp_task,
            **single_cell_exp_task, **structure_task}


async def summarize_gene_analysis(
    gene_id: str,
    gene_task: dict,
    deepgenome_out: str = dgc.DEEPGENOME_OUT,
    result_template: str = dgc.TEMPLATE,
    analysis_url: str = dgc.ANALYSIS_URL,
    region: str = dgc.ANALYSIS_REGION,
    download_path: str = dgc.DOWNLOAD_PATH,
    access_key_id: str = sc.AccessKeyID.get_secret_value(),
    secret_access_key: str = sc.SecretAccessKey.get_secret_value(),
    obs_server: str = dgc.OBS_SERVER,
    bucket_name: str = dgc.BUCKET_NAME,
    marker: Optional[str] = dgc.DOWNLOAD_MARKER,
    max_keys: int = dgc.DOWNLOAD_MAX_KEYS,
    timeout: float = dgc.TIMEOUT,
    retriable_codes: List[int] = dgc.RETRIABLE_CODES,
    max_retries: int = dgc.MAX_RETRIES,
    max_poll: float = dgc.MAX_POLL,
) -> str:
    """Summarize the results of a comprehensive gene analysis.

    This function waits for the completion of all analysis tasks, downloads
    the results from OBS, and generates a summary report. It reads template
    files and replaces placeholders with actual results.

    Args:
        gene_id: The identifier of the gene analyzed.
        gene_task: A dictionary containing the task information for all
            analyses.
        deepgenome_out: The output directory for deep genome results.
        result_template: The path to the result template.
        analysis_url: The URL for the analysis service.
        region: The region for the analysis service.
        download_path: The path to download files.
        access_key_id: The access key identifier for OBS.
        secret_access_key: The secret access key for OBS.
        obs_server: The OBS server URL.
        bucket_name: The OBS bucket name.
        marker: The marker for downloading files.
        max_keys: The maximum number of keys to download.
        timeout: Request timeout in seconds.
        retriable_codes: List of HTTP status codes that trigger a retry.
        max_retries: Maximum number of retry attempts.
        max_poll: Maximum duration in seconds to monitor the task.

    Returns:
        The path to the generated summary report file.
    """
    target_map = {
        'smep_task': ['.out', '.summary'],
        'smoc_task': ['.csv', '.summary'],
        'evolution_task': ['.md', '.png', '.summary'],
        'protein_structure_task':
            ['sample_0.cif', 'sample_0.json', '.summary'],
        'promoter_task': ['.png', '.summary'],
        'single_cell_task': ['.png', '.summary'],
        'tissues_task': ['.png', '.summary'],
        'cultivars_task': ['.png', '.summary'],
        'genotypes_task': ['.png', '.summary'],
        'treatments_task': ['.png', '.summary'],
    }

    async def wait_and_download(task_name: str, task_dict: str) -> str:
        _ = await wait_for_completion(
            task_id=task_dict['task_id'],
            analysis_url=analysis_url,
            region=region,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
            poll_interval=randint(300, 600),
            max_poll=max_poll,
            )
        obs_output_path = task_dict['output_dir'].split('/obs/phytomni/')[-1]
        deque(download_obs_out(
            task_dir=gene_id,
            obs_output_path=obs_output_path,
            download_path=download_path,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            obs_server=obs_server,
            target_file_feature=target_map[task_name],
            bucket_name=bucket_name,
            marker=marker,
            max_keys=max_keys,
            if_download_all=False,
        ), maxlen=0)
        return f'{task_name} results download succeed.'

    _ = await asyncio.gather(*[
        wait_and_download(task_name, task_dict)
        for task_name, task_dict in gene_task.items()
    ])

    out_path = Path(f'{deepgenome_out}/{gene_id}')
    with open(result_template, 'r', encoding='utf-8') as fi:
        gene_results = fi.read()
    gene_results = gene_results.replace('[Gene Name]', gene_id)
    try:
        target_file = next(out_path.rglob('*tree.png')).name
        tree_img_path = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('TREE_IMG', tree_img_path)
        with open(out_path / f'{gene_id}_tree.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[TREE]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![Phylogenetic Tree](TREE_IMG)', '')
        gene_results = gene_results.replace('[TREE]', 'None Results')
    try:
        with open(out_path / f'{gene_id}_domain.md',
                  'r', encoding='utf-8') as domain_f:
            domain = domain_f.read()
            gene_results = gene_results.replace('[markdown table]', domain)
        with open(out_path / f'{gene_id}_domain.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[DOMAIN]', summary)
    except (FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace('[markdown table]', '')
        gene_results = gene_results.replace('[DOMAIN]', 'None Results')

    try:
        target_file = next(out_path.rglob('*tissues.png')).name
        tissue_img = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('TISSUE_IMG', tissue_img)
        with open(out_path / f'{gene_id}_tissues.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[TISSUE]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![Tissue Expression](TISSUE_IMG)', '')
        gene_results = gene_results.replace('[TISSUE]', 'None Results')
    try:
        target_file = next(out_path.rglob('*cultivars.png')).name
        cultivar_img = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('CULTIVAR_IMG', cultivar_img)
        with open(out_path / f'{gene_id}_cultivars.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[CULTIVAR]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![Cultivar Expression](CULTIVAR_IMG)', '')
        gene_results = gene_results.replace('[CULTIVAR]', 'None Results')
    try:
        target_file = next(out_path.rglob('*genotypes.png')).name
        genotype_img = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('MUTANT_IMG', genotype_img)
        with open(out_path / f'{gene_id}_genotypes.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[MUTANT]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![Mutant Expression](MUTANT_IMG)', '')
        gene_results = gene_results.replace('[MUTANT]', 'None Results')
    try:
        target_file = next(out_path.rglob('*treatments.png')).name
        treatment_img = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('TREATMENT_IMG', treatment_img)
        with open(out_path / f'{gene_id}_treatments.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[TREATMENT]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![Treatment Expression](TREATMENT_IMG)', '')
        gene_results = gene_results.replace('[TREATMENT]', 'None Results')

    try:
        target_file = next(out_path.rglob('*_umap.png')).name
        sc_umap = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('UMAP_IMG', sc_umap)
        target_file = next(out_path.rglob('*_violin_plot.png')).name
        sc_violin = f'{gene_id}/{target_file}'
        gene_results = gene_results.replace('VIOLIN_IMG', sc_violin)
        with open(out_path / f'{gene_id}_single_cell.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            gene_results = gene_results.replace('[SINGLE_CELL]', summary)
    except (StopIteration, FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace('![UMAP Plot](UMAP_IMG)', '')
        gene_results = gene_results.replace('![Violin Plot](VIOLIN_IMG)', '')
        gene_results = gene_results.replace('[SINGLE_CELL]', 'None Results')

    try:
        protein_structure_files = list(out_path.glob(
            '*_seed_101_sample_0.cif'))
        if len(protein_structure_files) == 0:
            gene_results = gene_results.replace(
                '![3D Structure](STRUCTURE_IMG)', '')
            gene_results = gene_results.replace('[STRUCTURE]', 'None Results')
        else:
            for structure_path in protein_structure_files:
                structure_file = f'{gene_id}/{structure_path.name}'
                gene_results = gene_results.replace(
                    'STRUCTURE_IMG', structure_file)
                gene_results = gene_results.replace(
                    '**Interpretation:**\n[STRUCTURE]',
                    '![3D Structure](STRUCTURE_IMG)\n'
                    '**Interpretation:**\n[STRUCTURE]')
            gene_results = gene_results.replace(
                '![3D Structure](STRUCTURE_IMG)\n'
                '**Interpretation:**\n[STRUCTURE]',
                '**Interpretation:**\n[STRUCTURE]')
            with open(out_path / f'{gene_id}_structure.summary',
                      'r', encoding='utf-8') as summary_file:
                summary = summary_file.read()
                gene_results = gene_results.replace('[STRUCTURE]', summary)
    except (FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace(
            '![3D Structure](STRUCTURE_IMG)', '')
        gene_results = gene_results.replace('[STRUCTURE]', 'None Results')

    epic_summary = ''
    try:
        with open(out_path / f'{gene_id}_smep.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            epic_summary += summary
    except (FileNotFoundError, OSError, IOError):
        epic_summary += ''
    try:
        with open(out_path / f'{gene_id}_smoc.summary',
                  'r', encoding='utf-8') as summary_file:
            summary = summary_file.read()
            epic_summary += summary
    except (FileNotFoundError, OSError, IOError):
        epic_summary += ''
    gene_results = gene_results.replace('[EPIC]', epic_summary)

    try:
        motif_file_num = len(list(out_path.glob('*_logo.png')))
        if motif_file_num == 0:
            gene_results = gene_results.replace('![Motif](MOTIF_IMG)', '')
            gene_results = gene_results.replace('[MOTIF]', 'None Results')
        else:
            for index in range(motif_file_num):
                motif_file = f'{gene_id}/motif_{index+1}_logo.png'
                gene_results = gene_results.replace('MOTIF_IMG', motif_file)
                gene_results = gene_results.replace(
                    '**Interpretation:**\n[MOTIF]',
                    '![Motif](MOTIF_IMG)\n**Interpretation:**\n[MOTIF]')
            gene_results = gene_results.replace(
                '![Motif](MOTIF_IMG)\n**Interpretation:**\n[MOTIF]',
                '**Interpretation:**\n[MOTIF]')
            with open(out_path / f'{gene_id}_motif.summary',
                      'r', encoding='utf-8') as summary_file:
                summary = summary_file.read()
                gene_results = gene_results.replace('[MOTIF]', summary)
    except (FileNotFoundError, OSError, IOError):
        gene_results = gene_results.replace('![Motif](MOTIF_IMG)', '')
        gene_results = gene_results.replace('[MOTIF]', 'None Results')
    with open(f'{deepgenome_out}/{gene_id}_results.md',
              'w', encoding='utf-8') as fo:
        fo.write(gene_results)
    return f'{deepgenome_out}/{gene_id}_results.md'


def find_species_code(species: str):
    """Find the species code for a given species name.

    This function searches the SPECIES_CODE_MAP to find the species code
    corresponding to the provided species name.

    Args:
        species: The name of the species.

    Returns:
        The species code if found, otherwise None.
    """
    for species_code, description in SPECIES_CODE_MAP.items():
        if species.lower() in description.lower():
            return species_code
    return None


def prepend_to_file(filename, text):
    """Prepend text to the beginning of a file.

    This function reads the existing content of a file, then writes the
    provided text followed by the original content back to the file.

    Args:
        filename: The name of the file to prepend to.
        text: The text to prepend to the file.
    """
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            original_content = f.read()
    except FileNotFoundError:
        original_content = ''
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(text + original_content)
    return None
