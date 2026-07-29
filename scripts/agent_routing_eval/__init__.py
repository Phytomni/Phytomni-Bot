# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Contracts and validation helpers for agent-routing evaluation datasets."""

from . import dataset as _dataset
from . import metrics as _metrics
from . import reporting as _reporting
from . import runner as _runner

AgentRoutingCase = _dataset.AgentRoutingCase
DatasetValidationError = _dataset.DatasetValidationError
load_dataset = _dataset.load_dataset
validate_dataset = _dataset.validate_dataset
validate_dataset_pair = _dataset.validate_dataset_pair
verify_workbook_sources = _dataset.verify_workbook_sources
EvaluationIncompleteError = _runner.EvaluationIncompleteError
RunOutcome = _runner.RunOutcome
RunnerOptions = _runner.RunnerOptions
run_evaluation = _runner.run_evaluation
NO_MAJORITY = _metrics.NO_MAJORITY
compute_metrics = _metrics.compute_metrics
thresholds_pass = _metrics.thresholds_pass
GitState = _reporting.GitState
ReportContext = _reporting.ReportContext
build_report = _reporting.build_report
collect_git_state = _reporting.collect_git_state
dataset_sha256 = _reporting.dataset_sha256
description_sha256 = _reporting.description_sha256
provider_endpoint_sha256 = _reporting.provider_endpoint_sha256
write_report_pair = _reporting.write_report_pair

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
    "GitState",
    "ReportContext",
    "build_report",
    "collect_git_state",
    "dataset_sha256",
    "description_sha256",
    "provider_endpoint_sha256",
    "write_report_pair",
]
