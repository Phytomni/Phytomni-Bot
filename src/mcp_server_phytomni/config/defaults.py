# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from pathlib import Path
from typing import Dict, List, Literal, Optional, Union

from pydantic import Field
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

    MAX_TOKENS: int = Field(_MAX_TOKENS)

    PROMPT_FILE: str = Field(str(PROMPT_PATH))
    PROMPT_PATH: str = Field("system/ai4ps")

    TEMP_DIR: str = Field(str(TEMP_PATH))
    TIMEOUT: float = Field(600)
    RETRIABLE_CODES: List[int] = Field([429, 500, 502, 503, 504])
    MAX_RETRIES: int = Field(5)
    MAX_CONCURRENCY: int = Field(4)
    MAX_WORKERS: int = Field(4)

    TOKEN_URL: str = Field(
        "https://iam.cn-southwest-2.myhuaweicloud.com/v3/auth/tokens"
    )
    REGION: str = Field("cn-southwest-2")

    RETRIEVE_URL: str = Field(
        "http://1.95.74.240:8000/v1/koosearch/experience/search"
    )
    RERANK_URL: str = Field("http://1.95.74.240:8000/app/search/v1/rerank")
    DATABASE_URL: str = Field(
        "https://dataartsinsight.cn-southwest-2.myhuaweicloud.com/v1/"
        "6e939452a68f487f873c457f1953cf55/nl-query"
    )
    ANALYSIS_URL: str = Field(
        "https://eihealth.cn-east-3.myhuaweicloud.com/v1/"
        "f9afc0650aec4f9cbc7af24e9e199e77/eihealth-projects/"
        "6d50805e-8546-4c8b-a3c0-f7aa8b82bb74/jobs"
    )
    ANALYSIS_REGION: str = Field("cn-east-3")

    REPO_ID: str = Field("a34b2477-a4b1-4a30-8726-77bbf66ca048")
    REPO_ID_DICT: Dict[str, int] = Field(
        {
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
    )

    WORKSPACE_ID: str = Field("6e939452a68f487f873c457f1953cf55")
    SUBJECT_ID: str = Field("f6798956-2ce3-45a3-8dd1-cac242287531")

    OBS_SERVER: str = Field("https://obs.cn-east-3.myhuaweicloud.com")
    BUCKET_NAME: str = Field("phytomni")
    PART_SIZT: int = Field(16777216)
    TASK_NUM: int = Field(8)

    POLL_INTERVAL: float = Field(300)
    MAX_POLL: float = Field(86400)


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

    USER: str = Field("test")
    TEMPERATURE: float = Field(0.3)
    TOP_P: float = Field(1)
    PRESENCE_PENALTY: float = Field(0)
    FREQUENCY_PENALTY: float = Field(0)
    N: int = Field(1)
    REASONING_EFFORT: Literal["low", "medium", "high"] = Field("high")
    RESPONSE_FORMAT: Dict[str, Union[str, Dict]] = Field(
        {"type": "json_object"}
    )
    STREAM: bool = Field(False)


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

    PAGE_NUM: int = Field(1)
    PAGE_SIZE: int = Field(int(_MAX_TOKENS / 512))
    TOP_N: int = Field(int(_MAX_TOKENS / 512))
    FILTER_STRING: Optional[str] = Field(None)
    SCOPE: Literal["both", "doc", "keyword"] = Field("both")
    EXTRA_REPO_IDS: Optional[List[str]] = Field(None)
    SCORE_THRESHOLD: float = Field(0)
    RERANK_BATCH_SIZE: int = Field(128)


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

    NEED_INSIGHT: bool = Field(False)
    SIMPLIFY_RESPONSE: bool = Field(True)
    DIALOG_ID: str = Field("")
    DATA_REPO_ID: str = Field("a1ea209d-1a00-4d10-9cc5-d1fd8cd490cc")
    DATA_PAGE_SIZE: int = Field(3)


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

    OUTPUT_DIR: str = Field("/obs/phytomni/agent_data/test/")
    COMPUTE_RESOURCE: Literal["small", "medium", "large"] = Field("small")
    TASK_NAME: str = Field("analyst-agents-task")
    RESOURCE: Dict[str, Dict[str, int]] = Field(
        {
            "small": {"cpu": 4, "memory": 16},
            "medium": {"cpu": 8, "memory": 32},
            "large": {"cpu": 16, "memory": 64},
        }
    )
    APP_ID: Dict[str, str] = Field(
        {
            "small": "fa83143f-5e07-11f0-bbb4-fa163e7f72d1",
            "medium": "1d1b3dc5-5e08-11f0-bbb4-fa163e7f72d1",
            "large": "31b31aac-5e08-11f0-bbb4-fa163e7f72d1",
        }
    )
    EXECUTE_CODE: bool = Field(True)
    USER_ID: str = Field("")
    CREATE_DIR: bool = Field(True)
    DOWNLOAD_PATH: str = Field(str(DOWNLOAD_PATH))
    DOWNLOAD_MARKER: Optional[str] = Field(None)
    DOWNLOAD_MAX_KEYS: int = Field(1000)
    IF_DOWNLOAD_ALL: bool = Field(True)
    TARGET_FILE_FEATURE: List[str] = Field([""])
    PRE_PREPARED_DATA_PATH: str = Field(str(PRE_PREPARED_DATA_PATH))
    POLL_INTERVAL: float = Field(300)
    MAX_POLL: float = Field(86400)


class ReviewConfig(KnowledgeConfig):
    """Configuration settings for review generation or related tasks.

    Inherits settings from `KnowledgeConfig`.

    Attributes:
        TOP_N (int): Number of top-scoring results to retrieve or consider
            specifically for review purposes.
    """

    TOP_N: int = Field(int(_MAX_TOKENS / 2048))


class GeneNetworkConfig(AnalystConfig):
    """Configuration settings specific to gene network tasks.

    Inherits settings from `AnalystConfig`.
    """

    DEEPGENOME_DATA: str = Field(str(PRE_PREPARED_DATA_PATH))


class DeepGenomeConfig(DataConfig, AnalystConfig):
    """Configuration settings specific to gene function analysis tasks.

    Inherits settings from both `DataConfig` and `AnalystConfig`.

    Attributes:
        MAX_CONCURRENCY (int): Maximum number of concurrent operations allowed
            for tasks related to gene function analysis.
    """

    DEEPGENOME_DATA: str = Field(str(PRE_PREPARED_DATA_PATH))
    DEEPGENOME_OUT: str = Field(str(DOWNLOAD_PATH))
    BI_URL: str = Field("https://phytomni.cn/api/data")
    CREATE_TASK_URL: str = Field(
        "http://1.95.48.200:8082/v1/nky/server/" "create_task"
    )
    UPDATE_TASK_URL: str = Field(
        "http://1.95.48.200:8082/v1/nky/server/" "update_task"
    )
    BATCH: bool = Field(True)
    EPIC_TYPE: str = Field("6mA")
    PROTOCOL_REPO_ID: str = Field("44ad28b5-5c3b-4a02-8e8c-7fb4903424cb")


class DigitalDesignConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.
    """

    DEEPGENOME_DATA: str = Field(str(PRE_PREPARED_DATA_PATH))


class InSilicoResearchConfig(AnalystConfig):
    """Configuration settings specific to in silico research tasks.

    Inherits settings from `AnalystConfig`.
    """


class EnvironmentConfig(AnalystConfig):
    """Configuration settings specific to digital design tasks.

    Inherits settings from `AnalystConfig`.
    """

    ENVIRONMENT_DATA: str = Field(str(PRE_PREPARED_DATA_PATH))
    REGION_CODE: str = Field(str(PRE_PREPARED_REGION_PATH))
