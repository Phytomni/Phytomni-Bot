# Phytomni-Bot

Phytomni-Bot is a server for plant science research that provides a suite of specialized AI agents to assist with various tasks, from basic Q&A to complex bioinformatics analysis. It is built on the Multi-Capability Protocol (MCP) and exposes different agents as tools that can be called remotely.

## Features

The server provides the following agents:

- **ChatAgent**: Provides concise explanations for foundational or single-domain questions in plant biology (e.g., definitions, basic mechanisms) using the LLM's internal knowledge. Not recommended for multi-dimensional agricultural optimization or climate adaptation strategies.
- **KnowledgeAgent**: Retrieves and synthesizes information from plant science literature, patents, and books through RAG (Retrieval-Augmented Generation) pipelines, providing evidence-supported answers.
- **DataAgent**: Executes structured queries on botanical databases (e.g., species traits, experimental data) using SQL interfaces, returning precise numerical/statistical results.
- **AnalystAgent**: Initiates computational workflows (e.g., sequence alignment, phylogenetic analysis) through integrated bioinformatics platforms for genomic/proteomic investigations.
- **ReviewAgent**: Conducts a comprehensive and in-depth investigation into a user's query, synthesizing information from a wide range of scientific literature and other relevant sources to produce a structured review or detailed report. Ideal for when a broad understanding, critical assessment, or an extensive overview of a complex topic is required, going beyond targeted Q&A or data retrieval.
- **DeepGenomeAgent**: Integrates functional annotations from plant multi-omics databases (GO, KEGG, etc.) with experimental evidence mined from literature, generating comparative summaries with experimental evidence.
- **InSilicoResearchAgent**: Decomposes a complete scientific paper by analyzing its methodology and results, producing a structured, sequential list of high-level tasks designed for computational replication.

## Getting Started

### Prerequisites

- Python >= 3.12

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/Phytomni-Bot.git
   cd Phytomni-Bot
   ```
2. Install the dependencies using [uv](https://github.com/astral-sh/uv):
   ```bash
   uv venv --python=3.12 .venv
   source .venv/bin/activate
   # NOTE: Be sure to use 'uv pip install' rather than just 'pip install' to install packages in this virtual environment
   ```

### Configuration

1. Create a `.env` file from the example:
   ```bash
   cp src/mcp_server_phytomni/.env.example src/mcp_server_phytomni/.env
   ```
2. Edit the `.env` file with your credentials and other settings.

### Running the Server

To start the server, run the following command:

```bash
python src/mcp_server_phytomni/server.py
```

## Usage

The server communicates over standard I/O using the MCP protocol. It exposes two main endpoints:

- `list_tools`: Returns a list of available agents and their schemas.
- `call_tool`: Executes a specific agent with the given parameters.

## Dependencies

- [esdk-obs-python](https://pypi.org/project/esdk-obs-python/)
- [httpx](https://pypi.org/project/httpx/)
- [mcp](https://pypi.org/project/mcp/)
- [openai](https://pypi.org/project/openai/)
- [pydantic](https://pypi.org/project/pydantic/)
- [pydantic-settings](https://pypi.org/project/pydantic-settings/)
- [python-dotenv](https://pypi.org/project/python-dotenv/)

## License

This project is licensed under the LICENSE file.
