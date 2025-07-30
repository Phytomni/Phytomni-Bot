# Phytomni-Bot

Phytomni-Bot is a comprehensive Model Context Protocol (MCP) server for plant science research that provides a suite of specialized AI agents to assist with various tasks, from basic Q&A to complex bioinformatics analysis. Built on modern Python architecture with robust configuration management and cloud integration.

## 🌱 Features

The server provides the following specialized agents:

- **ChatAgent**: Provides interactive conversational interface with document processing capabilities, supporting multiple file formats (PPTX, DOCX, XLSX, PDF, etc.) for foundational plant biology questions and multi-modal research assistance.

- **KnowledgeAgent**: Advanced literature retrieval and synthesis using RAG (Retrieval-Augmented Generation) pipelines, providing evidence-supported answers from plant science literature, patents, and research databases with multi-repository support and intelligent reranking.

- **DataAgent**: Executes natural language to SQL queries on botanical databases, providing precise numerical and statistical results with query optimization and conversation context support.

- **AnalystAgent**: Orchestrates complex computational workflows including sequence alignment, phylogenetic analysis, and bioinformatics pipelines through integrated cloud platforms with comprehensive task management and monitoring.

- **ReviewAgent**: Conducts comprehensive multi-dimensional research investigations, synthesizing information from diverse scientific literature sources to produce structured reviews and detailed reports with automatic research dimension extraction.

- **DeepGenomeAgent**: Advanced gene function analysis integrating multi-omics data (GO, KEGG, etc.) with experimental evidence from literature, supporting 65+ plant species with comprehensive functional annotation and comparative analysis.

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
   pip install pytest pytest-asyncio ipykernel
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
   OPENAI_API_KEY=your_openai_api_key
   ACCESS_KEY_ID=your_obs_access_key
   SECRET_ACCESS_KEY=your_obs_secret_key

   # Optional custom endpoints
   MODEL_URL=your_model_endpoint
   OBS_SERVER=your_obs_server
   BUCKET_NAME=your_bucket_name
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

The server communicates over standard I/O using the MCP protocol and exposes the following endpoints:

- **`list_tools`**: Returns a list of available agents and their JSON schemas
- **`call_tool`**: Executes a specific agent with the given parameters

### Example MCP Client Integration

```python
from mcp import ClientSession

async def example_usage():
    async with ClientSession() as session:
        # List available tools
        tools = await session.list_tools()
        print(f"Available tools: {[tool.name for tool in tools.tools]}")

        # Use ChatAgent for basic Q&A
        result = await session.call_tool(
            "ChatAgent",
            {
                "user_query": "What is the role of chlorophyll in photosynthesis?",
                "obs_file_list": []
            }
        )

        # Use DeepGenomeAgent for gene analysis
        gene_result = await session.call_tool(
            "DeepGenomeAgent",
            {
                "species": "arabidopsis",
                "gene_list": ["AT1G01010"],
                "user_id": "researcher_001"
            }
        )

# Run the example
import asyncio
asyncio.run(example_usage())
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
- **pytest** (>=8.4.1): Testing framework
- **pytest-asyncio** (>=1.0.0): Async testing support
- **ipykernel** (>=6.30.0): Jupyter notebook support

## 🧪 Development

### Running Tests

```bash
# Using uv
uv run pytest
uv run pytest --cov=src/mcp_server_phytomni

# Using conda/mamba environment
conda activate phytomni-bot  # or: mamba activate phytomni-bot
pytest
pytest --cov=src/mcp_server_phytomni

# Using traditional virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pytest
pytest --cov=src/mcp_server_phytomni
```

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
