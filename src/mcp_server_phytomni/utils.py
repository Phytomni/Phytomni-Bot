# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import time
import traceback
import uuid
from json import dumps, load
from math import ceil
from pathlib import Path
from re import sub
from typing import Dict, Optional
from yaml import safe_load

from httpx import AsyncClient, HTTPError, Timeout
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR
from obs import ObsClient

from .config.defaults import ServerConfig
from .config.settings import SensitiveConfig

serverconfig = ServerConfig()
sensitiveconfig = SensitiveConfig().load()


async def get_token(timeout: float = serverconfig.TIMEOUT,
                    region: str = serverconfig.REGION) -> str:
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
        password = sensitiveconfig.USER_PASSWORD.get_secret_value()
        data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": sensitiveconfig.USER_NAME,
                            "password": password,
                            "domain": {"name": sensitiveconfig.DOMAIN_NAME},
                        },
                    },
                },
                "scope": {"project": {"name": region}},
            },
        }
        try:
            response = await client.post(
                serverconfig.TOKEN_URL,
                headers={"Content-Type": "application/json"},
                data=dumps(data),
                timeout=timeout)
            response.raise_for_status()
            return response.headers["X-Subject-Token"]
        except HTTPError as e:
            raise McpError(ErrorData(
                code=INTERNAL_ERROR,
                message=f"Failed to get token: {str(e)}")) from e


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
        raise FileNotFoundError(f"Template file not found: {template_file}")
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
                "Must specify template_str for multi-level templates")


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
            raise ValueError(f"Missing parameter: {param_name}")
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


def get_data_list(data_file: str,
                  analysis_type: str,
                  species: str) -> list:
    """Generate ready-to-use prompt from template components.

    Combines template loading and rendering in one workflow:
    1. Load base template from YAML file
    2. Apply parameter substitutions

    Args:
        data_file: data_list_file for json format
        analysis_type: analysis_type[evolution_analysis, deepgo2_analysis, structure_analysis,
                                    prompter_analysis, protein_design_analysis, gene_expression_analysis, ppi_analysis]
        species: 65 species ...

    Returns:
        data_list for analysis
    """
    try:
        with open(data_file) as f:
            data = load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_file}")
    try:
        analysis_data_list = data[analysis_type]
    except KeyError:
        raise KeyError(f"Analysis type not found: {analysis_type}")
    try:
        data_list = analysis_data_list[species]
    except KeyError:
        raise KeyError(f"Species not found: {species}")

    return data_list


def upload_analyst_agents_data(analyst_agents_datapath):

    ak = 'HPUAE0AYEP7UL66O2S77'
    sk = 'Fdu2iGnvPoVdYbiFrVZlJEbYxm7HnMPJteLPcs3U'
    server = "https://obs.cn-east-3.myhuaweicloud.com"
    # 创建obsClient实例
    # 如果使用临时AKSK和SecurityToken访问OBS，需要在创建实例时通过security_token参数指定securityToken值
    obsClient = ObsClient(access_key_id=ak, secret_access_key=sk, server=server)
    try:
        # 上传对象的附加头域
        headers = PutObjectHeader()
        # 【可选】待上传对象的MIME类型
        headers.contentType = 'text/plain'
        bucketName = "phytomni"
        # 对象名，即上传后的文件名
        object_file = analyst_agents_datapath.split('/')[-1]
        objectKey = f"agent_data/tmp_data/{object_file}"
        print(objectKey)
        # objectKey = "objectname"
        # 待上传文件的完整路径，如aa/bb.txt
        file_path = f"./{object_file}"
        # 上传文件的自定义元数据
        metadata = {'meta1': 'value1', 'meta2': 'value2'}
        # 文件上传
        resp = obsClient.putFile(bucketName, objectKey, file_path, metadata, headers)
        # 返回码为2xx时，接口调用成功，否则接口调用失败
        if resp.status < 300:
            print('Put File Succeeded')
            print('requestId:', resp.requestId)
            print('etag:', resp.body.etag)
            print('versionId:', resp.body.versionId)
            print('storageClass:', resp.body.storageClass)
            obj_filepath = f"{bucketName}:/{objectKey}"

            return obj_filepath
        else:
            print('Put File Failed')
            print('requestId:', resp.requestId)
            print('errorCode:', resp.errorCode)
            print('errorMessage:', resp.errorMessage)

            raise OSError
    except:
        print('Put File Failed')
        print(traceback.format_exc())

        raise OSError


def delete_analyst_agents_data(analyst_agents_datapath):

    # 推荐通过环境变量获取AKSK，这里也可以使用其他外部引入方式传入，如果使用硬编码可能会存在泄露风险
    # 您可以登录访问管理控制台获取访问密钥AK/SK，获取方式请参见https://support.huaweicloud.com/usermanual-ca/ca_01_0003.html
    ak = 'HPUAE0AYEP7UL66O2S77'
    sk = 'Fdu2iGnvPoVdYbiFrVZlJEbYxm7HnMPJteLPcs3U'
    # 【可选】如果使用临时AKSK和SecurityToken访问OBS，则同样推荐通过环境变量获取
    # security_token = os.getenv("SecurityToken")
    # server填写Bucket对应的Endpoint, 这里以华北-北京四为例，其他地区请按实际情况填写
    server = "https://obs.cn-east-3.myhuaweicloud.com"
    # 创建obsClient实例
    # 如果使用临时AKSK和SecurityToken访问OBS，需要在创建实例时通过security_token参数指定securityToken值
    obsClient = ObsClient(access_key_id=ak, secret_access_key=sk, server=server)
    try:
        bucketName = "phytomni"
        objectKey = analyst_agents_datapath
        # 如果删除多版本对象请指定versionId,未开启多版本则不传
        # versionId = 'G001119522F0D8Cxxxxxxxxxxxxxxxxx'
        # 删除单个对象
        resp = obsClient.deleteObject(bucketName, objectKey)
        # 返回码为2xx时，接口调用成功，否则接口调用失败
        if resp.status < 300:
            print('Delete Object Succeeded')
            print('requestId:', resp.requestId)
            print('deleteMarker:', resp.body.deleteMarker)
            print('versionId:', resp.body.versionId)
        else:
            print('Delete Object Failed')
            print('requestId:', resp.requestId)
            print('errorCode:', resp.errorCode)
            print('errorMessage:', resp.errorMessage)
    except:
        print('Delete Object Failed')
        print(traceback.format_exc())


def create_output_dir(user_id: str, 
                      task: str) -> str:
    # 创建输出文件夹
    ak = 'HPUAE0AYEP7UL66O2S77'
    sk = 'Fdu2iGnvPoVdYbiFrVZlJEbYxm7HnMPJteLPcs3U'
    server = "https://obs.cn-east-3.myhuaweicloud.com"
    # 创建obsClient实例
    obsClient = ObsClient(access_key_id=ak, secret_access_key=sk, server=server)
    # print(f"agent_data/user_data/{user_id}/output/{task}_{int(time.time())}_{uuid.uuid1()}/")
    try:
        bucketName = "genomiagent"
        # 上传后的文件夹名称，以'/'结尾
        output_dir = f"agent_data/user_data/{user_id}/output/{task}_{int(time.time())}_{uuid.uuid1()}/"
        # 对象名以'/'结尾即为创建文件夹，创建文件夹时为了不造成意料之外的计费，请不要上传内容
        resp = obsClient.putContent(bucketName, output_dir, content=None)
        # 返回码为2xx时，接口调用成功，否则接口调用失败
        if resp.status < 300:
            print('Put Content Succeeded')
            print('requestId:', resp.requestId)
            output = f"obs://genomiagent/{output_dir}"
            
            return output
        else:
            print('Put Content Failed')
            print('requestId:', resp.requestId)
            print('errorCode:', resp.errorCode)
            print('errorMessage:', resp.errorMessage)

            raise OSError(f'Create Out dir Error')
    except:
        print('Put Content Failed')
        print(traceback.format_exc())

        raise OSError('Create Out dir Error')
