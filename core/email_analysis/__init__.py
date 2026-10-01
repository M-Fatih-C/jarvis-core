"""Intelligent local email analysis and task extraction package for Jarvis."""

from core.email_analysis.analyzer import EmailAnalyzer
from core.email_analysis.schemas import (
    DeadlineConfidence,
    EmailAnalysisResult,
    EmailCategory,
    ImportanceLevel,
    TaskPriority,
    TaskProposal,
)
from core.email_analysis.task_extractor import TaskExtractor

__all__ = [
    "DeadlineConfidence",
    "EmailAnalysisResult",
    "EmailAnalyzer",
    "EmailCategory",
    "ImportanceLevel",
    "TaskExtractor",
    "TaskPriority",
    "TaskProposal",
]
