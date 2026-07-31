# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contracts and validation helpers for agent-routing evaluation datasets."""

from . import dataset as _dataset
from . import metrics as _metrics
from . import reporting as _reporting
from . import runner as _runner

AgentRoutingCase = getattr(_dataset, "AgentRoutingCase")
DatasetValidationError = getattr(_dataset, "DatasetValidationError")
load_dataset = getattr(_dataset, "load_dataset")
validate_dataset = getattr(_dataset, "validate_dataset")
validate_dataset_pair = getattr(_dataset, "validate_dataset_pair")
verify_workbook_sources = getattr(_dataset, "verify_workbook_sources")
EvaluationIncompleteError = getattr(_runner, "EvaluationIncompleteError")
RunOutcome = getattr(_runner, "RunOutcome")
RunnerOptions = getattr(_runner, "RunnerOptions")
run_evaluation = getattr(_runner, "run_evaluation")
NO_MAJORITY = getattr(_metrics, "NO_MAJORITY")
compute_metrics = getattr(_metrics, "compute_metrics")
thresholds_pass = getattr(_metrics, "thresholds_pass")
GitState = getattr(_reporting, "GitState")
ReportContext = getattr(_reporting, "ReportContext")
build_report = getattr(_reporting, "build_report")
collect_git_state = getattr(_reporting, "collect_git_state")
dataset_sha256 = getattr(_reporting, "dataset_sha256")
description_sha256 = getattr(_reporting, "description_sha256")
provider_endpoint_sha256 = getattr(_reporting, "provider_endpoint_sha256")
write_report_pair = getattr(_reporting, "write_report_pair")

_REPORTING_EXPORT_NAMES = tuple(
    name for name in _reporting.__all__ if name != "RunCommand"
)

__all__ = [
    "AgentRoutingCase",
    "DatasetValidationError",
    "load_dataset",
    "validate_dataset",
    "validate_dataset_pair",
    "verify_workbook_sources",
    "EvaluationIncompleteError",
    "RunOutcome",
    "RunnerOptions",
    "run_evaluation",
    "NO_MAJORITY",
    "compute_metrics",
    "thresholds_pass",
    *_REPORTING_EXPORT_NAMES,
]
