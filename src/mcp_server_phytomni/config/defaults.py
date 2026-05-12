# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Default non-secret configuration for Phytomni agents.

Classes: ServerConfig, ChatConfig, KnowledgeConfig, DataConfig, AnalystConfig,
    ReviewConfig, BriefGeneConfig, GeneNetworkConfig, DeepGenomeConfig,
    DigitalDesignConfig, InSilicoResearchConfig, EnvironmentConfig,
    SpeciesDataIndex, RegionMap.
"""

from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from pydantic import RootModel
from pydantic_settings import BaseSettings

_MAX_TOKENS = 65536
PARENT_PATH = Path(__file__).parent.parent
PROMPT_PATH = PARENT_PATH / "config/.prompts.yaml"
PRE_PREPARED_DATA_PATH = PARENT_PATH / "config/species_data_list.json"
PRE_PREPARED_REGION_PATH = PARENT_PATH / "config/region_map.json"
DOWNLOAD_PATH = PARENT_PATH / ".out"
TEMP_PATH = PARENT_PATH / ".temp"


class ServerConfig(BaseSettings):
    """Configuration settings for server-level parameters and service
        endpoints.

    Attributes:
        PROMPT_FILE (str): Path to the main prompt template file.
        PROMPT_PATH (str): Default path or key within the prompt file to
            retrieve specific system prompts.
        TIMEOUT (float): General request timeout in seconds for API calls.
        RETRIABLE_CODES (List[int]): List of HTTP status codes that trigger
            retries for API calls.
        MAX_RETRIES (int): Maximum number of retry attempts for API calls.
        TOKEN_URL (str): URL for obtaining authentication tokens.
        REGION (str): Cloud service region.
        RETRIEVE_URL (str): URL for the document retrieval service.
        RERANK_URL (str): URL for the document reranking service.
        DATABASE_URL (str): URL for the database query service (e.g., NLQ).
        ANALYSIS_URL (str): URL for the workflow analysis service.
        ANALYSIS_REGION (str): Analyst agents cloud service region.
        REPO_ID (str): A default or primary repository identifier.
        REPO_ID_DICT (Dict[str, int]): Dictionary mapping repository IDs to
            associated integer values (e.g., page sizes or token limits).
        WORKSPACE_ID (str): Identifier for the workspace.
        SUBJECT_ID (str): Identifier for the database subject or schema.
        POLL_INTERVAL (float): Interval in seconds for polling the status of
            long-running tasks.
        MAX_POLL (float): Maximum duration in seconds for polling the status
            of long-running tasks.
    """

    MAX_TOKENS: int = _MAX_TOKENS

    PROMPT_FILE: str = str(PROMPT_PATH)
    PROMPT_PATH: str = "system/ai4ps"

    TEMP_DIR: str = str(TEMP_PATH)
    TIMEOUT: float = 600
    RETRIABLE_CODES: List[int] = [429, 500, 502, 503, 504]
    MAX_RETRIES: int = 5
    MAX_CONCURRENCY: int = 4
    MAX_WORKERS: int = 4

    TOKEN_URL: str = (
        "https://iam.cn-southwest-2.myhuaweicloud.com/v3/auth/tokens"
    )
    REGION: str = "cn-southwest-2"

    RETRIEVE_URL: str = (
        "http://1.95.74.240:8000/v1/koosearch/experience/search"
    )
    RERANK_URL: str = "http://1.95.74.240:8000/app/search/v1/rerank"
    DATABASE_URL: str = (
        "https://dataartsinsight.cn-southwest-2.myhuaweicloud.com/v1/"
        "6e939452a68f487f873c457f1953cf55/nl-query"
    )
    ANALYSIS_URL: str = (
        "https://eihealth.cn-east-3.myhuaweicloud.com/v1/"
        "f9afc0650aec4f9cbc7af24e9e199e77/eihealth-projects/"
        "6d50805e-8546-4c8b-a3c0-f7aa8b82bb74/jobs"
    )
    ANALYSIS_REGION: str = "cn-east-3"

    REPO_ID: str = "a34b2477-a4b1-4a30-8726-77bbf66ca048"
    REPO_ID_DICT: Dict[str, int] = {
        "a34b2477-a4b1-4a30-8726-77bbf66ca048": int(_MAX_TOKENS / 512),
        "ec3be998-43a8-483e-a2d8-029c9161431b": int(_MAX_TOKENS / 1024),
        "d38a792f-58a3-4aff-b521-f04dc6bd06b3": int(_MAX_TOKENS / 1024),
        "c6aa6922-15ec-44bb-bfaa-3bc85ed4d1a2": int(_MAX_TOKENS / 512),
        "708b0cf8-fa4d-4ad0-885f-ca3bf4565cda": int(_MAX_TOKENS / 512),
        "7f747eb7-223c-42fa-9cae-431a0bb1a999": int(_MAX_TOKENS / 1024),
        "44ad28b5-5c3b-4a02-8e8c-7fb4903424cb": int(_MAX_TOKENS / 1024),
        "8d7ff2ab-91dd-4d8a-a07d-93729e8c05aa": int(_MAX_TOKENS / 1024),
        "7ee75b57-bf09-4124-9e3a-ddb2070ccb2c": int(_MAX_TOKENS / 512),
    }

    WORKSPACE_ID: str = "6e939452a68f487f873c457f1953cf55"
    SUBJECT_ID: str = "f6798956-2ce3-45a3-8dd1-cac242287531"

    OBS_SERVER: str = "https://obs.cn-east-3.myhuaweicloud.com"
    BUCKET_NAME: str = "phytomni"
    PART_SIZE: int = 16777216
    TASK_NUM: int = 8

    POLL_INTERVAL: float = 300
    MAX_POLL: float = 86400


class ChatConfig(ServerConfig):
    """Configuration settings for chat interactions with a language model.

    Inherits settings from `ServerConfig`.

    Attributes:
        USER (str): User identifier for API interactions, particularly for
            chat or LLM services.
        TEMPERATURE (float): Sampling temperature for language model responses
            (controls randomness). Higher values mean more random responses.
        TOP_P (float): Nucleus sampling parameter for language model responses
            (controls diversity). Considers tokens with cumulative probability
            mass up to `TOP_P`.
        MAX_TOKENS (int): Maximum number of tokens to generate in language
            model responses.
        PRESENCE_PENALTY (float): Penalty applied to new tokens based on their
            presence in the text so far, discouraging repetition of concepts.
            Values range from -2.0 to 2.0.
        FREQUENCY_PENALTY (float): Penalty applied to new tokens based on their
            frequency in the text so far, discouraging repetition of exact
            words/phrases. Values range from -2.0 to 2.0.
        N (int): Number of completion choices to generate for each input.
        REASONING_EFFORT (Literal['low', 'medium', 'high']): Specifies the
            level of reasoning effort for the language model.
        RESPONSE_FORMAT (Dict[str, Union[str, Dict]]): Desired response format
            from the language model. For example, `{'type': 'json_object'}`
            to request a JSON response.
        STREAM (bool): Flag to enable or disable streaming of responses from
            the language model. If True, responses are sent as a series of
            events.
    """

    USER: str = "test"
    TEMPERATURE: float = 0.3
    TOP_P: float = 1
    PRESENCE_PENALTY: float = 0
    FREQUENCY_PENALTY: float = 0
    N: int = 1
    REASONING_EFFORT: Literal["low", "medium", "high"] = "high"
    RESPONSE_FORMAT: Dict[str, Union[str, Dict]] = {"type": "json_object"}
    STREAM: bool = False


class KnowledgeConfig(ChatConfig):
    """Configuration settings for knowledge retrieval and document search.

    Inherits settings from `ChatConfig`.

    Attributes:
        PAGE_NUM (int): Page number for paginated results from retrieval
            services.
        PAGE_SIZE (int): Number of items per page for paginated results from
            retrieval services.
        TOP_N (int): Number of top-scoring results to retrieve or consider.
        FILTER_STRING (Optional[str]): Optional filter criteria string for
            metadata filtering during retrieval.
        SCOPE (Literal['both', 'doc', 'keyword']): Scope of search for
            retrieval operations. 'both' searches documents and keywords,
            'doc' searches only documents, 'keyword' searches only keywords.
        EXTRA_REPO_IDS (Optional[List[str]]): Optional list of additional
            repository IDs to include in retrieval.
        SCORE_THRESHOLD (float): Minimum relevance score threshold for
            retrieved items. Results below this threshold are typically
            discarded.
        RERANK_BATCH_SIZE (int): Batch size for reranking operations, if
            reranking is applied to retrieved documents.
    """

    PAGE_NUM: int = 1
    PAGE_SIZE: int = int(_MAX_TOKENS / 512)
    TOP_N: int = int(_MAX_TOKENS / 512)
    FILTER_STRING: Optional[str] = None
    SCOPE: Literal["both", "doc", "keyword"] = "both"
    EXTRA_REPO_IDS: Optional[List[str]] = None
    SCORE_THRESHOLD: float = 0
    RERANK_BATCH_SIZE: int = 128


class DataConfig(KnowledgeConfig):
    """Configuration settings for database query operations.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        NEED_INSIGHT (bool): Flag indicating whether to generate insights from
            database queries.
        SIMPLIFY_RESPONSE (bool): Flag indicating whether to simplify the
            structure of database query responses.
        DIALOG_ID (str): Conversation ID for multi-turn context.
        DATA_REPO_ID (str): The ID of the primary knowledge repository to
            search for Data-Agent RAG functionality.
        DATA_PAGE_SIZE (int): Number of items per page for paginated results
            from retrieval services specific to Data-Agent operations.
    """

    NEED_INSIGHT: bool = False
    SIMPLIFY_RESPONSE: bool = True
    DIALOG_ID: str = ""
    DATA_REPO_ID: str = "a1ea209d-1a00-4d10-9cc5-d1fd8cd490cc"
    DATA_PAGE_SIZE: int = 3


class AnalystConfig(KnowledgeConfig):
    """Configuration settings for analysis workflows.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        OUTPUT_DIR (str): Output directory path for storing results of
            analysis or operations (e.g., an OBS path).
        EXECUTE_CODE (bool): Flag indicating whether code execution is
            permitted during an analysis operation.
        POLL_INTERVAL (float): Interval in seconds for polling the status of
            long-running analysis tasks.
        MAX_POLL (float): Maximum duration in seconds for polling the status
            of long-running analysis tasks.
        COMPUTE_RESOURCE: compute resource type.
        TASK_NAME: task name in ai4s platform.
        APP_ID: app id in difference compute resource
        RESOURCE: cpu and memory information in difference compute resource.
    """

    TOOL_REPO_ID: str = "381d8f6c-89d9-468d-9531-a0ced46c7d02"
    TOOL_PAGE_NUM: int = 1
    TOOL_PAGE_SIZE: int = 2
    OUTPUT_DIR: str = "/obs/phytomni/agent_data/test/"
    COMPUTE_RESOURCE: Literal["small", "medium", "large"] = "small"
    TASK_NAME: str = "analyst-agents-task"
    RESOURCE: Dict[str, Dict[str, int]] = {
        "small": {"cpu": 4, "memory": 8},
        "medium": {"cpu": 8, "memory": 16},
        "large": {"cpu": 16, "memory": 48},
    }
    APP_ID: Dict[str, str] = {
        "small": "e71c5415-4c67-11f1-bbb4-fa163e7f72d1",
        "medium": "2f0a0495-4c68-11f1-bbb4-fa163e7f72d1",
        "large": "624753c3-4c68-11f1-bbb4-fa163e7f72d1",
    }
    EXECUTE_CODE: bool = True
    USER_ID: str = ""
    CREATE_DIR: bool = True
    DOWNLOAD_PATH: str = str(DOWNLOAD_PATH)
    DOWNLOAD_MARKER: Optional[str] = None
    DOWNLOAD_MAX_KEYS: int = 1000
    IF_DOWNLOAD_ALL: bool = True
    TARGET_FILE_FEATURE: List[str] = [""]
    PRE_PREPARED_DATA_PATH: str = str(PRE_PREPARED_DATA_PATH)
    POLL_INTERVAL: float = 300
    MAX_POLL: float = 86400


class ReviewConfig(KnowledgeConfig):
    """Configuration settings for review generation or related tasks.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        TOP_N (int): Number of top-scoring results to retrieve or consider
            specifically for review purposes.
    """

    TOP_N: int = int(_MAX_TOKENS / 2048)


class BriefGeneConfig(KnowledgeConfig):
    """Configuration settings for brief gene function reports.

    This agent combines direct BI database annotation lookup with literature
    retrieval, so it inherits the knowledge retrieval and chat defaults while
    adding the BI API endpoint.

    Attributes:
        BI_URL: BI API endpoint used for direct gene annotation lookup.
        TOP_N: Maximum number of literature retrieval results to keep.
    """

    BI_URL: str = "https://phytomni.cn/api/data"
    TOP_N: int = int(_MAX_TOKENS / 2048)


class GeneNetworkConfig(AnalystConfig):
    """Configuration settings specific to gene network tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        DEEPGENOME_DATA: Static species metadata file used for task prompts.
    """

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class DeepGenomeConfig(DataConfig, AnalystConfig):
    """Configuration settings specific to gene function analysis tasks.

    Inherits settings from both `DataConfig` and `AnalystConfig`.

    Attributes:
        MAX_CONCURRENCY (int): Maximum number of concurrent operations allowed
            for tasks related to gene function analysis.
    """

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)
    DEEPGENOME_OUT: str = str(DOWNLOAD_PATH)
    BI_URL: str = "https://phytomni.cn/api/data"
    CREATE_TASK_URL: str = "http://1.95.48.200:8082/v1/nky/server/create_task"
    UPDATE_TASK_URL: str = "http://1.95.48.200:8082/v1/nky/server/update_task"
    BATCH: bool = True
    EPIC_TYPE: str = "6mA"
    PROTOCOL_REPO_ID: str = "44ad28b5-5c3b-4a02-8e8c-7fb4903424cb"
    PROTOCOL_PAGE_SIZE: int = 128
    SPA_REPO_ID: str = "4a533117-9416-4e8b-b7cc-27b448a90095"
    SPA_FAQ_URL: str = (
        "http://1.95.74.240:8000/v1/koosearch/repos/{repo_id}/faqs"
    )


class DigitalDesignConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        DEEPGENOME_DATA: Static species metadata file used for task prompts.
    """

    DEEPGENOME_DATA: str = str(PRE_PREPARED_DATA_PATH)


class InSilicoResearchConfig(AnalystConfig):
    """Configuration settings specific to in silico research tasks.

    Inherits settings from `AnalystConfig`.
    """


class EnvironmentConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.

    Attributes:
        ENVIRONMENT_DATA: Static environment metadata file.
        REGION_CODE: Static region metadata file.
    """

    ENVIRONMENT_DATA: str = str(PRE_PREPARED_DATA_PATH)
    REGION_CODE: str = str(PRE_PREPARED_REGION_PATH)


SpeciesEntryValue = Union[str, Dict[str, str]]


class SpeciesDataIndex(
    RootModel[Dict[str, Dict[str, Dict[str, SpeciesEntryValue]]]]
):
    """Validation schema for species_data_list.json.

    The file follows the shape
    ``{analysis_type: {species_name: {key: leaf}}}`` where ``leaf`` is
    either an OBS file description string or a sub-dict mapping further
    keys (e.g. ``cultivars``, ``tissues``) to ``{file_path: description}``.
    """

    def analysis_types(self) -> List[str]:
        """Return the top-level analysis type keys.

        Returns:
            List of analysis-type keys present in the validated index.
        """
        return list(self.root.keys())

    def species_for(self, analysis_type: str) -> List[str]:
        """Return the species keys configured under ``analysis_type``.

        Args:
            analysis_type: Analysis type key (e.g. ``"evolution_analysis"``).

        Returns:
            List of species keys under the requested analysis type.
        """
        return list(self.root[analysis_type].keys())


class RegionMap(RootModel[Dict[str, Dict[str, Dict[str, str]]]]):
    """Validation schema for region_map.json.

    The file maps {province: {city: {district: region_code}}}; every leaf
    value is a pipe-delimited string like ``"310000|310000|310114"``.
    """

    def provinces(self) -> List[str]:
        """Return the top-level province keys.

        Returns:
            List of province keys present in the validated map.
        """
        return list(self.root.keys())

    def cities_for(self, province: str) -> List[str]:
        """Return the city keys configured under ``province``.

        Args:
            province: Province key (e.g. ``"北京市"``).

        Returns:
            List of city keys under the requested province.
        """
        return list(self.root[province].keys())
