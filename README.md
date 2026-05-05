# Phytomni-Bot

Phytomni-Bot is a comprehensive Model Context Protocol (MCP) server for plant science research that provides a suite of specialized AI agents to assist with various tasks, from basic Q&A to complex bioinformatics analysis. Built on modern Python architecture with robust configuration management and cloud integration.

## 🌱 Features

The server provides the following specialized agents:

- **ChatAgent**: Provides interactive conversational interface with document processing capabilities, supporting multiple file formats (PPTX, DOCX, XLSX, PDF, etc.) for foundational plant biology questions and multi-modal research assistance.

- **KnowledgeAgent**: Advanced literature retrieval and synthesis using RAG (Retrieval-Augmented Generation) pipelines, providing evidence-supported answers from plant science literature, patents, and research databases with multi-repository support and intelligent reranking.

- **DataAgent**: Executes natural language to SQL queries on botanical databases, providing precise numerical and statistical results with query optimization and conversation context support.

- **AnalystAgent**: Orchestrates complex computational workflows including sequence alignment, phylogenetic analysis, and bioinformatics pipelines through integrated cloud platforms with comprehensive task management and monitoring.

- **ReviewAgent**: Conducts comprehensive multi-dimensional research investigations, synthesizing information from diverse scientific literature sources to produce structured reviews and detailed reports with automatic research dimension extraction.

- **DeepGenomeAgent**: Advanced gene function analysis integrating multi-omics data (GO, MapMan, etc.) with experimental evidence from literature, supporting 65+ plant species with comprehensive functional annotation and comparative analysis.

- **InSilicoResearchAgent**: Decomposes scientific papers by analyzing methodology and results, producing structured, computational-ready task lists for research replication and validation.

- **DigitalDesignAgent**: Specialized protein structure analysis and design workflows for plant biotechnology applications with computational modeling support.

- **GeneNetworkAgent**: Comprehensive gene network analysis including interaction prediction, co-expression analysis, and regulatory network characterization for plant genomics research.

## 🚀 Getting Started

### Prerequisites

- Python >= 3.12
- One of the following package managers:
  - [uv](https://github.com/astral-sh/uv) (recommended for fast installation)
  - [conda](https://docs.conda.io/en/latest/) or [mamba](https://mamba.readthedocs.io/en/latest/) (recommended for scientific computing)

### Installation

**Typical Installation Time:** 10-30 minutes (including downloading dependency packages)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Phytomni/Phytomni-Bot.git
   cd Phytomni-Bot
   ```

2. **Choose your installation method:**

#### Option A: Using uv (Recommended)
   ```bash
   # Create virtual environment with Python 3.12
   uv venv --python=3.12 .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate

   # Install dependencies
   uv pip install -e .

   # For development (optional)
   uv pip install -e ".[dev]"
   ```

#### Option B: Using conda/mamba
   ```bash
   # Create and activate conda environment
   conda env create -f environment.yml
   conda activate phytomni-bot

   # Or using mamba (faster)
   mamba env create -f environment.yml
   mamba activate phytomni-bot

   # Install the package in development mode
   pip install -e .

   # For development (optional)
   pip install -e ".[dev]"
   ```

#### Option C: Using pip (Traditional)
   ```bash
   # Create virtual environment
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate

   # Install dependencies
   pip install -e .

   # For development (optional)
   pip install -e ".[dev]"
   ```

### Configuration

1. **Create configuration file:**
   ```bash
   cp src/mcp_server_phytomni/config/.env.example src/mcp_server_phytomni/config/.env
   ```

2. **Edit the `.env` file with your credentials:**
   ```bash
   # Required API keys and credentials
   DOMAIN_NAME=your_domain_name
   USER_NAME=your_user_name
   USER_PASSWORD=your_user_password
   AccessKeyID=your_AccessKeyID
   SecretAccessKey=your_SecretAccessKey
   BASE_URL=your_base_url
   MODEL_ID=your_model_id
   API_KEY=your_api_key
   CODER_URL=your_coder_url
   CODER_MODEL=your_coder_model
   CODER_API_KEY=your_coder_api_key
   ```

   Refer to the `.env.example` file for complete configuration options.

### Running the Server

To start the MCP server:

```bash
# From the project root directory
python -m src.mcp_server_phytomni.server

# Or if installed as package
python -c "import asyncio; from mcp_server_phytomni.server import serve; asyncio.run(serve())"
```

## 📖 Usage

### MCP Protocol Overview

Phytomni-Bot implements the **Model Context Protocol (MCP)** for AI agent orchestration. The server communicates using **JSON-RPC 2.0** over **standard I/O (stdio)** transport, providing a standardized interface for client applications.

**Core MCP Methods:**
- **`tools/list`**: Discover available agents and their parameter schemas
- **`tools/call`**: Execute specific agents with validated parameters
- **`initialize`**: Protocol handshake and capability exchange

**Transport Protocol:**
- **Communication**: JSON-RPC 2.0 messages
- **Transport**: Standard input/output streams
- **Session Management**: Async context managers with automatic cleanup

### Configuration Prerequisites

Before using the agents, ensure your `.env` file is properly configured:

```bash
# Required: Copy and edit configuration
cp src/mcp_server_phytomni/config/.env.example src/mcp_server_phytomni/config/.env

# Edit with your credentials:
DOMAIN_NAME=your_domain_name
USER_NAME=your_user_name
USER_PASSWORD=your_user_password
AccessKeyID=your_AccessKeyID
SecretAccessKey=your_SecretAccessKey
BASE_URL=your_base_url
MODEL_ID=your_model_id
API_KEY=your_api_key
CODER_URL=your_coder_url
CODER_MODEL=your_coder_model
CODER_API_KEY=your_coder_api_key
```

**File Processing Note**: For agents that support document processing, provide absolute OBS paths in the format: `/obs/phytomni/path/to/document.pdf`

### Client Integration Examples

#### Python MCP SDK Client

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    # Configure server parameters
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "src.mcp_server_phytomni.server"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize connection
            await session.initialize()

            # Discover available tools
            tools = await session.list_tools()
            print(f"Available agents: {[t.name for t in tools.tools]}")

            # Example: Use ChatAgent
            result = await session.call_tool(
                "ChatAgent",
                {
                    "user_query": "Explain the Calvin cycle in plant photosynthesis"
                }
            )
            print(f"Response: {result.content[0].text}")

            # Example: Use DeepGenomeAgent
            gene_result = await session.call_tool(
                "DeepGenomeAgent",
                {
                    "species_code": "ath",  # Arabidopsis thaliana
                    "gene_id": "AT1G01010"
                }
            )
            print(f"Gene analysis: {gene_result.content[0].text}")

if __name__ == "__main__":
    asyncio.run(main())
```

#### CLI Usage Pattern

```bash
# Start server in background
python -m src.mcp_server_phytomni.server &

# Use MCP CLI tools (requires mcp-cli installation)
mcp list-tools
mcp call ChatAgent '{"user_query": "What are plant hormones?"}'
```

#### Direct Function Call (for development)

```python
import asyncio
from src.mcp_server_phytomni.server import serve
from mcp.client.stdio import stdio_client

async def direct_usage():
    # Start server and connect directly
    server_task = asyncio.create_task(serve())

    # Your client code here
    await asyncio.sleep(1)  # Give server time to start

    # Cancel server when done
    server_task.cancel()

asyncio.run(direct_usage())
```

### Available Agents Overview

The server provides **9 specialized AI agents** for plant science research:

**Quick Response (1-5 minutes):**
- **ChatAgent**: Foundational plant biology Q&A with document processing
- **KnowledgeAgent**: Literature retrieval and RAG-based synthesis
- **DataAgent**: Natural language to SQL queries on botanical databases

**Medium Response (5-10 minutes):**
- **ReviewAgent**: Comprehensive multi-dimensional research investigations

**Long Response (1-3 hours):**
- **AnalystAgent**: Bioinformatics workflow orchestration
- **GeneNetworkAgent**: Gene interaction and regulatory network analysis
- **DigitalDesignAgent**: Protein structure analysis and design workflows

**Extended Response (24 hours):**
- **DeepGenomeAgent**: Multi-omics gene function analysis (65+ plant species)
- **InSilicoResearchAgent**: Scientific paper methodology decomposition

### Error Handling and Best Practices

#### MCP-Compliant Error Responses

```python
try:
    result = await session.call_tool("ChatAgent", {
        "user_query": "invalid query"
    })
except Exception as e:
    # MCP errors include structured error data
    if hasattr(e, 'data'):
        print(f"Error Code: {e.code}")
        print(f"Error Message: {e.message}")
        print(f"Error Details: {e.data}")
    else:
        print(f"Unexpected error: {e}")
```

#### Resource Management

```python
import asyncio
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_session():
    """Proper session management with cleanup."""
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "src.mcp_server_phytomni.server"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                yield session
            finally:
                # Automatic cleanup handled by context managers
                pass

# Usage
async with managed_session() as session:
    result = await session.call_tool("ChatAgent", {
        "user_query": "Your query here"
    })
```

#### Performance Optimization

- **Use Async Patterns**: Always use async/await for non-blocking operations
- **Batch Operations**: Group multiple tool calls when possible
- **Timeout Configuration**: Set appropriate timeouts for long-running agents
- **Connection Reuse**: Maintain sessions for multiple related operations

### Troubleshooting Common Issues

**Configuration Problems:**
- Ensure all 11 environment variables are set in `.env`
- Verify OBS credentials for file processing operations
- Check model URLs and API keys are accessible

**Connection Issues:**
- Confirm Python 3.12+ environment
- Verify server startup: `python -m src.mcp_server_phytomni.server`
- Check stdio transport compatibility with your client

**Performance Issues:**
- Network latency affects response times
- Large file uploads require sufficient bandwidth
- Consider agent-specific time expectations (1 min to 24 hours)

For comprehensive demo examples and expected outputs, see the **🎯 Demo** section below.

## 🎯 Demo

This section provides comprehensive demonstration examples for all **9 specialized AI agents** in Phytomni-Bot. Each demo includes usage instructions, expected outputs, and realistic run time expectations.

### 📋 Demo Setup

Before running demos, ensure your environment is configured:

```bash
# 1. Copy demo configuration template
cp demo_data/.env.demo src/mcp_server_phytomni/config/.env

# 2. Edit the .env file with your actual credentials
# 3. Start the MCP server
python -m src.mcp_server_phytomni.server
```

### Quick Response Agents (1-5 minutes)

#### ChatAgent - Plant Biology Q&A with Document Processing
**Purpose**: Foundational plant science questions and document analysis

**Prerequisites**: Basic configuration, optional document files

**Example Usage**:
```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def demo_chat_agent():
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "src.mcp_server_phytomni.server"]
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Basic Q&A without documents
            result = await session.call_tool("ChatAgent", {
                "user_query": "Explain the process of photosynthesis in C3 plants"
            })
            print(f"Response: {result.content[0].text}")

asyncio.run/demo_chat_agent())
```

**Expected Output**:
- Text response explaining photosynthesis mechanism
- Document summary with key points and insights
- Structured answer with scientific accuracy

**Expected Run Time**: 1-3 minutes for text queries, 2-5 minutes with documents

---

#### KnowledgeAgent - Literature Retrieval and RAG Synthesis
**Purpose**: Evidence-based answers from plant science literature, patents, and research databases

**Prerequisites**: Access to literature databases, optional reference documents

**Example Usage**:
```python
async def demo_knowledgeagent():
    # ... session setup as above ...

    # Literature-based research
    result = await session.call_tool("KnowledgeAgent", {
        "user_query": "How does high ambient temperature modulate the oligomerization and kinase activity of BSK3/BIN2 in Arabidopsis thaliana?"
    })
    print(f"Literature Synthesis: {result.content[0].text}")

asyncio.run(demo_knowledgeagent())
```

**Expected Output**:
- Comprehensive synthesis with citations
- Multi-source information integration
- Evidence-backed conclusions with references

**Expected Run Time**: 3-5 minutes (literature search + synthesis)

---

#### DataAgent - Natural Language to SQL Queries
**Purpose**: Extract numerical and statistical data from botanical databases

**Prerequisites**: Database access configured in environment

**Example Usage**:
```python
async def demo_dataagent():
    # ... session setup as above ...

    # Query plant trait data
    result = await session.call_tool("DataAgent", {
        "user_query": "What are the homologous genes of AT1G75370 in wheat?"
    })
    print(f"Data Query Result: {result.content[0].text}")

asyncio.run(demo_dataagent())
```

**Expected Output**:
- Structured numerical data with units
- Tabular format with statistical summaries
- SQL query results formatted as readable text

**Expected Run Time**: 1-2 minutes per query

---

### Medium Response Agents (5-10 minutes)

#### ReviewAgent - Multi-Dimensional Research Investigation
**Purpose**: Comprehensive reviews and detailed reports across multiple research dimensions

**Prerequisites**: Broad access to scientific literature sources

**Example Usage**:
```python
async def demo_reviewagent():
    # ... session setup as above ...

    # Comprehensive research review
    result = await session.call_tool("ReviewAgent", {
        "user_query": "How novel epigenetic modifications, such as DNA 6mA, RNA m6A, and RNA m5C, regulate environmental responses in plants?"
    })
    print(f"Research Review: {result.content[0].text}")

asyncio.run(demo_reviewagent())
```

**Expected Output**:
- Structured review with multiple sections
- Critical analysis of current state
- Research gaps and future directions
- Comparative analysis across studies

**Expected Run Time**: 5-10 minutes (deep literature analysis)

---

### Long Response Agents (1-3 hours)

#### AnalystAgent - Bioinformatics Workflow Orchestration
**Purpose**: Automated execution of computational biology workflows (sequence alignment, phylogenetics, etc.)

**Prerequisites**: Cloud platform access, genomic data files

**Example Usage**:
```python
async def demo_analystagent():
    # ... session setup as above ...

    # Phylogenetic analysis
    result = await session.call_tool("AnalystAgent", {
        "goal_description": "Please help me to perform the callpeak analysis for rice ATAC-Seq data.",
        "data_list": {
            "/obs/phytomni/agent_data/raw_data/04.benchmark_data/PhytoBench-Analysis/PhytoBench-Analysis/genome/callsnp/data1_1.fq.gz": "pair-end fastq file 1",
            "/obs/phytomni/agent_data/raw_data/04.benchmark_data/PhytoBench-Analysis/PhytoBench-Analysis/genome/callsnp/data1_2.fq.gz": "pair-end fastq file 2",
        },
    })
    print(f"Analysis Results: {result.content[0].text}")

asyncio.run(demo_analystagent())
```

**Expected Output**:
- Workflow execution status and logs
- Phylogenetic trees and alignment results
- Statistical analysis outputs
- Visualization file references

**Expected Run Time**: 1-3 hours (computational workflow execution)

---

#### GeneNetworkAgent - Gene Interaction and Regulatory Network Analysis
**Purpose**: Analyze gene co-expression, regulatory networks, and interaction patterns

**Prerequisites**: Network analysis tools, gene expression data

**Example Usage**:
```python
async def demo_genenetwork():
    # ... session setup as above ...

    # Network analysis
    result = await session.call_tool("GeneNetworkAgent", {
        "species": "oryza sativa",
        "to_id": "TO:0000207"
    })
    print(f"Gene Network Analysis: {result.content[0].text}")

asyncio.run(demo_genenetwork())
```

**Expected Output**:
- Interaction network topology
- Co-expression clusters
- Regulatory pathway mappings
- Hub gene identification
- Network visualization references

**Expected Run Time**: 1-3 hours (network construction + analysis)

---

#### DigitalDesignAgent - Protein Structure Analysis and Design
**Purpose**: Computational protein modeling, structure prediction, and design workflows

**Prerequisites**: Access to computational modeling platforms

**Example Usage**:
```python
async def demo_digitaldesign():
    # ... session setup as above ...

    # Protein design analysis
    result = await session.call_tool("DigitalDesignAgent", {
        "species": "glycine max",
        "gene_id": "GLYMA_11G228300"
    })
    print(f"Protein Design Analysis: {result.content[0].text}")

asyncio.run(demo_digitaldesign())
```

**Expected Output**:
- Protein structure predictions
- Functional domain analysis
- Design optimization suggestions
- Stability assessments
- Modeling file references

**Expected Run Time**: 1-3 hours (computational modeling + analysis)

---

### Extended Response Agents (24 hours)

#### DeepGenomeAgent - Multi-Omics Gene Function Analysis
**Purpose**: Integrate multi-omics data (GO, KEGG, etc.) with literature evidence for comprehensive gene analysis

**Prerequisites**: Access to multi-omics databases, species code knowledge

**Example Usage**:
```python
async def demo_deepgenome():
    # ... session setup as above ...

    # Arabidopsis gene analysis
    result = await session.call_tool("DeepGenomeAgent", {
        "species_code": "ath",  # Arabidopsis thaliana
        "gene_id": "AT1G01010"  # Specific gene identifier
    })
    print(f"Gene Function Analysis: {result.content[0].text}")

    # Rice gene analysis
    rice_result = await session.call_tool("DeepGenomeAgent", {
        "species_code": "osa",  # Oryza sativa (rice)
        "gene_id": "LOC_Os01g01010"
    })
    print(f"Rice Gene Analysis: {rice_result.content[0].text}")

asyncio.run(demo_deepgenome())
```

**Expected Output**:
- Comprehensive functional annotation
- GO term enrichment analysis
- KEGG pathway mappings
- Experimental evidence synthesis
- Cross-species comparative analysis

**Expected Run Time**: 12-24 hours (multi-omics data integration + literature mining)

**Supported Species Codes**:
- `ath`: Arabidopsis thaliana, `osa`: Oryza sativa (rice), `zma`: Zea mays (maize)
- `sly`: Solanum lycopersicum (tomato), `gma`: Glycine max (soybean)
- Plus 60+ additional plant species (see agent parameter documentation)

---

#### InSilicoResearchAgent - Scientific Paper Methodology Decomposition
**Purpose**: Analyze research papers and extract computational task lists for replication

**Prerequisites**: Research paper in PDF format

**Example Usage**:
```python
async def demo_insilicoresearch():
    # ... session setup as above ...

    # Paper analysis
    result = await session.call_tool("InSilicoResearchAgent", {
        "user_query": "Decompose this plant genomics paper into computational tasks for replication",
        "data_list": {},
        "obs_file_list": ["/obs/phytomni/agent_data/user_data/test_upload/xieshang0608@gmail.com/Plant Cell-2021-Prediction of conserved and variable heat and cold stress response in maize using cis-regulatory information.pdf"]
    })
    print(f"Methodology Decomposition: {result.content[0].text}")

asyncio.run(demo_insilicoresearch())
```

**Expected Output**:
- Structured list of computational tasks
- Algorithm and parameter specifications
- Data requirements and formats
- Step-by-step replication workflow

**Expected Run Time**: 8-24 hours (paper analysis + task extraction)

---

### 🔧 Troubleshooting Demo Issues

**Common Problems**:
1. **Configuration Errors**: Verify all 11 environment variables in `.env`
2. **File Access Issues**: Ensure OBS files are accessible with correct paths
3. **Timeout Issues**: Adjust timeouts for long-running agents
4. **Resource Limits**: Monitor memory and CPU usage during extended analyses

**Debug Mode**:
```python
# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Test individual components
tools = await session.list_tools()
print(f"Available tools: {[tool.name for tool in tools.tools]}")
```

## 🏗️ Architecture

The system features a hierarchical configuration architecture:

- **Base Configuration**: `ServerConfig` provides core networking and authentication
- **Specialized Configs**: Each agent inherits and extends base configuration
- **Secure Management**: `SensitiveConfig` handles credential management
- **Multi-Service Integration**: OBS storage, language models, and bioinformatics platforms

## 📦 Dependencies

Core dependencies (managed by package managers):

- **[esdk-obs-python](https://pypi.org/project/esdk-obs-python/)** (>=3.25.3): Object Storage Service integration
- **[httpx](https://pypi.org/project/httpx/)** (>=0.28.1): Async HTTP client
- **[markitdown](https://pypi.org/project/markitdown/)** (>=0.1.2): Document processing
- **[mcp](https://pypi.org/project/mcp/)** (>=1.12.0): Model Context Protocol implementation  
- **[openai](https://pypi.org/project/openai/)** (>=1.93.2): Language model integration
- **[pydantic](https://pypi.org/project/pydantic/)** (>=2.11.7): Data validation and settings
- **[pydantic-settings](https://pypi.org/project/pydantic-settings/)** (>=2.10.1): Configuration management
- **[python-dotenv](https://pypi.org/project/python-dotenv/)** (>=1.1.1): Environment variable loading
- **[pyyaml](https://pypi.org/project/pyyaml/)** (>=6.0.2): YAML processing

Development dependencies:
- **black** (>=26.3.1): Code formatter
- **flake8** (>=7.3.0): Style checker
- **ipykernel** (>=7.2.0): Jupyter notebook support
- **mypy** (>=1.20.2): Static type checker
- **pylint** (>=4.0.5): Python linter
- **pytest** (>=9.0.3): Testing framework
- **pytest-asyncio** (>=1.3.0): Async testing support
- **ruff** (>=0.15.12): Fast Python linter

## 💻 System Requirements

### Software Dependencies

**Operating Systems:**
- **Linux distributions only**
  - Ubuntu 22.04 LTS
  - CentOS Stream 8
  - Huawei Cloud EulerOS 2.0 (tested environment)
  - Other Linux distributions may also be compatible

**Python Environment:**
- **Python**: 3.12.0 or later (required)
- **Package Manager**: uv 0.5.0 or later (recommended)
- Other package managers (conda, mamba, pip) may also work

### Tested Versions

**Test Environment:**
- **Operating System**: Huawei Cloud EulerOS 2.0 (x86_64)
- **Python Version**: 3.12.3
- **Package Manager**: uv 0.8.4
- **Installation Method**: uv-based virtual environment

The software has been tested with the above configuration and is known to work with Ubuntu 22.04 LTS and CentOS Stream 8.

### Hardware Requirements

**Current Test Environment:**
- **CPU Cores**: 64 threads
- **Memory**: 256 GB
- **Available Storage**: 100 GB

**Minimum Requirements:**
- Exact minimum hardware requirements are not yet determined
- The system is designed to work on standard Linux environments
- Performance may scale with available CPU cores and memory

**Recommended Configuration:**
- For optimal performance, multi-core processors with sufficient RAM are recommended
- Additional storage space may be required for large document processing and temporary files

### Special Requirements

- **Linux Environment**: Currently only supports Linux systems
- **Internet Connection**: Required for API calls and cloud service integration
- **Cloud Services**: Requires access to OpenAI API and compatible Object Storage Service (OBS/S3)

## 🧪 Development

### Code Quality

The project uses modern Python practices:
- Type hints throughout the codebase
- Pydantic for data validation
- Comprehensive docstrings (Google style)
- Async/await for concurrent operations

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with proper documentation
4. Add tests for new functionality
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## 📄 License

This project is licensed under the terms specified in the [LICENSE](LICENSE) file.

## 👥 Authors

- **Shang Xie** - xieshang0608@gmail.com
- **Yichao Mao** - maoyc_0316@163.com
- **Xiaofeng Gu** - guxiaofeng@caas.cn

**Copyright**: Biotechnology Research Institute, Chinese Academy of Agricultural Sciences, 2024-2025. All rights reserved.

---

For more information about the Model Context Protocol, visit [MCP Documentation](https://modelcontextprotocol.io/).
