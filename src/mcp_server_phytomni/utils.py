# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
from json import dumps
from math import ceil
from pathlib import Path
from re import sub
import time
from traceback import format_exc
from typing import Dict, Optional
from yaml import safe_load

from httpx import AsyncClient, HTTPError, Timeout
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import ObsClient

from .config.defaults import ServerConfig
from .config.settings import SensitiveConfig

serc = ServerConfig()
senc = SensitiveConfig().load()


async def get_token(timeout: float = serc.TIMEOUT,
                    region: str = serc.REGION) -> str:
    """Obtain X-Subject-Token for API authentication.

    Args:
        timeout: Total request timeout (5s minimum connection timeout)
            - Controls both connection and response phases

    Returns:
        X-Subject-Token string for authorization headers
        - Token validity period determined by IAM service

    Raises:
        McpError: With error code and message when:
            - Network connectivity issues
            - Invalid credentials
            - IAM service unavailability
    """
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        password = senc.USER_PASSWORD.get_secret_value()
        data = {
            'auth': {
                'identity': {
                    'methods': ['password'],
                    'password': {
                        'user': {
                            'name': senc.USER_NAME,
                            'password': password,
                            'domain': {'name': senc.DOMAIN_NAME},
                        },
                    },
                },
                'scope': {'project': {'name': region}},
            },
        }
        try:
            response = await client.post(
                serc.TOKEN_URL,
                headers={'Content-Type': 'application/json'},
                data=dumps(data),
                timeout=timeout)
            response.raise_for_status()
            return response.headers['X-Subject-Token']
        except HTTPError as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f'Failed to get token: {str(e)}')) from e


def load_template(template_file: str,
                  template_str: Optional[str] = None,
                  ) -> str:
    """Load template structure from YAML file with optional path navigation.

    Args:
        template_file: Path to YAML template file
            - Must be valid file path with read permissions
        template_str: Nested template path using '/' separators
            - e.g. "prompts/analysis" for multi-level YAML
            - Required when template has hierarchical structure

    Returns:
        Final template string from specified location in YAML

    Raises:
        FileNotFoundError: If template_file path is invalid
        KeyError: When template_str path doesn't exist in YAML
        ValueError: For ambiguous templates without template_str
    """
    template_file = Path(template_file)
    if not template_file.is_file():
        raise FileNotFoundError(f'Template file not found: {template_file}')
    with open(template_file, 'r', encoding='utf-8') as f:
        data = safe_load(f)
    if template_str:
        current = data
        for part in template_str.split('/'):
            if part not in current:
                raise KeyError(
                    f"Path '{part}' not found in template structure")
            current = current[part]
        return current
    else:
        if isinstance(data, dict) and len(data) == 1:
            return next(iter(data.values()))
        else:
            raise ValueError(
                'Must specify template_str for multi-level templates')


def render_template(template: str,
                    parameters: Optional[Dict[str, str]] = None
                    ) -> str:
    """Replace placeholders in template with actual parameters.

    Args:
        template: String containing {{parameter}} placeholders
        parameters: Key-value pairs for placeholder substitution
            - Keys must match placeholder names
            - Values will be stringified during replacement

    Returns:
        Fully rendered template with substituted values

    Raises:
        ValueError: When placeholder lacks matching parameter
    """
    if parameters is None:
        parameters = {}
    pattern = r'\{\{([^}]+)\}\}'

    def replacer(match):
        param_name = match.group(1).strip()
        if param_name not in parameters:
            raise ValueError(f'Missing parameter: {param_name}')
        return str(parameters[param_name])

    return sub(pattern, replacer, template)


def get_prompt(template_file: str,
               template_str: Optional[str] = None,
               parameters: Optional[Dict[str, str]] = None,
               ) -> str:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        template_file: See load_template()
        template_str: See load_template()
        parameters: See render_template()

    Returns:
        Final prompt ready for LLM input

    Raises:
        Exceptions from both load_template and render_template
    """
    if parameters is None:
        parameters = {}
    template = load_template(template_file, template_str)
    return render_template(template, parameters)


def download_obs_file(
    obs_file: str,
    server_dir: str,
    access_key_id: str = senc.AccessKeyID.get_secret_value(),
    secret_access_key: str = senc.SecretAccessKey.get_secret_value(),
    obs_server: str = serc.OBS_SERVER,
    bucket_name: str = serc.BUCKET_NAME,
    part_size: int = serc.PART_SIZT,
    task_num: int = serc.TASK_NUM,
    max_retries: int = serc.MAX_RETRIES,
) -> str:
    server_path = Path(server_dir)
    server_path.mkdir(parents=True, exist_ok=True)
    server_file = str(server_path / Path(obs_file).name)
    obs_client = ObsClient(access_key_id=access_key_id,
                           secret_access_key=secret_access_key,
                           server=obs_server)
    for attempt in range(max_retries + 1):
        try:
            download_response = obs_client.downloadFile(
                bucketName=bucket_name,
                objectKey=obs_file,
                downloadFile=server_file,
                partSize=part_size,
                taskNum=task_num,
                enableCheckpoint=True,
            )
            if download_response.status < 300:
                return server_file
            raise OSError(f'Download File Failed\n'
                          f'requestId: {download_response.requestId}\n'
                          f'errorCode: {download_response.errorCode}\n'
                          f'errorMessage: {download_response.errorMessage}')
        except Exception as exc:
            if attempt < max_retries:
                time.sleep(1.5 ** attempt)
                continue
            raise OSError(f'Download File Failed\n{format_exc()}') from exc


def split_list(lst, max_size: int = 128):
    """Splits a list into chunks of at most `max_size` elements each.

    The chunks are made as close in size as possible. The function divides
    the list into `ceil(len(lst) / max_size)` chunks and distributes the
    elements from the original list among them. If the input list is empty,
    an empty list of chunks is returned.

    Args:
        lst (list): The list to be split.
        max_size (int): The maximum size for any chunk. Defaults to `128`.

    Returns:
        list: A list of lists, where each inner list is a chunk of the
        original list. Returns an empty list if the input list is empty.
    """
    n = len(lst)
    if n == 0:
        return []

    num_chunks = ceil(n / max_size)
    base_size = n // num_chunks
    remainder = n % num_chunks

    chunks = []
    index = 0
    for i in range(num_chunks):
        chunk_size = base_size + 1 if i < remainder else base_size
        chunks.append(lst[index:index+chunk_size])
        index += chunk_size
    return chunks
