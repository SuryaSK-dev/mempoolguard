"""Label-Leakage Audit Subsystem for MempoolGuard.

Audits synthetic scenario generator logic against the canonical feature space,
documenting direct behavioral dependencies and reporting benchmark limitations.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("mempoolguard.models.audit")


class ScenarioDependency(BaseModel):
    """Documented relationship between a synthetic scenario and feature space."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    expected_label: int
    generator_behavior: str
    correlated_features: list[str]
    leakage_risk_level: str  # HIGH, MEDIUM, LOW
    explanation: str


class LabelLeakageAuditReport(BaseModel):
    """Structured report detailing label-to-feature dependencies and model limitations."""

    model_config = ConfigDict(extra="forbid")

    scenarios_audited: int
    direct_dependency_count: int
    scenario_dependencies: list[ScenarioDependency]
    summary_findings: str
    benchmark_limitation_statement: str


def run_label_leakage_audit() -> LabelLeakageAuditReport:
    """Perform a comprehensive audit of generator-to-feature dependencies across all 8 scenarios.

    Returns:
        Structured LabelLeakageAuditReport detailing findings and limitations.
    """
    dependencies = [
        ScenarioDependency(
            scenario_id="BENIGN_TRANSFER",
            expected_label=0,
            generator_behavior="Dispatches baseline ETH value transfers with standard network base fee.",
            correlated_features=[
                "gas_price_to_window_ratio",
                "is_burst_activity",
                "is_fan_out",
                "is_contract_call",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "Benign transfers exhibit near-unity gas ratios (approx 1.0), zero burst indicators, "
                "and zero calldata. Models easily classify these by the absence of anomaly flags."
            ),
        ),
        ScenarioDependency(
            scenario_id="REPEATED_TRANSFER",
            expected_label=1,
            generator_behavior="Sends rapid consecutive transfers between identical sender & receiver accounts.",
            correlated_features=[
                "is_repeated_transfer",
                "sender_receiver_tx_count",
                "sender_receiver_existing_relationship",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "Generator behavior directly increments sender_receiver_tx_count and trips is_repeated_transfer. "
                "The feature engine was specifically designed to capture this pattern."
            ),
        ),
        ScenarioDependency(
            scenario_id="GAS_SPIKE",
            expected_label=1,
            generator_behavior="Sets gas price to 25x the target block base fee simulating aggressive front-running.",
            correlated_features=[
                "gas_price_gwei",
                "gas_price_to_window_ratio",
                "max_fee_per_gas_gwei",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "The label is assigned based on fee inflation, which directly maps to gas_price_gwei. "
                "Trained models learn a clean threshold partition on this feature."
            ),
        ),
        ScenarioDependency(
            scenario_id="GAS_DEVIATION_LOW",
            expected_label=1,
            generator_behavior="Dispatches transactions with sub-base or zero priority fee.",
            correlated_features=[
                "gas_price_gwei",
                "gas_price_to_window_ratio",
            ],
            leakage_risk_level="MEDIUM",
            explanation=(
                "Sub-base fee transactions manifest directly as gas_price_gwei < 1.0. "
                "The classifier easily separates this distinct lower bound."
            ),
        ),
        ScenarioDependency(
            scenario_id="BURST_ACTIVITY",
            expected_label=1,
            generator_behavior="Submits 6 transactions within seconds from a single account.",
            correlated_features=[
                "window_tx_count_10s",
                "window_tx_count_60s",
                "is_burst_activity",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "Window counting features directly reflect the generator's rapid submission frequency. "
                "Models can trivially classify bursts using window_tx_count_10s >= 3."
            ),
        ),
        ScenarioDependency(
            scenario_id="FAN_OUT",
            expected_label=1,
            generator_behavior="Dispatches transfers to 5 distinct destination addresses within 60s.",
            correlated_features=[
                "window_unique_receivers_60s",
                "is_fan_out",
                "sender_out_degree",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "The scenario definition matches the formula for window_unique_receivers_60s. "
                "A strong direct correlation exists between the generator loop and graph out-degree."
            ),
        ),
        ScenarioDependency(
            scenario_id="REPEATED_INTERACTIONS",
            expected_label=1,
            generator_behavior="Cyclic interaction alternating transfers between sender and receiver.",
            correlated_features=[
                "sender_receiver_tx_count",
                "sender_receiver_existing_relationship",
            ],
            leakage_risk_level="MEDIUM",
            explanation=(
                "Historical pair interactions are recorded directly by the database history query, "
                "creating a strong feature signal."
            ),
        ),
        ScenarioDependency(
            scenario_id="CONTRACT_CALL",
            expected_label=1,
            generator_behavior="Attaches non-empty contract bytecode/calldata payload.",
            correlated_features=[
                "is_contract_call",
                "calldata_size_bytes",
                "function_selector_hash",
            ],
            leakage_risk_level="HIGH",
            explanation=(
                "Because benign transactions in this PoC have calldata_size_bytes == 0, any transaction "
                "with is_contract_call == 1 is easily separated by models."
            ),
        ),
    ]

    summary = (
        "Audit confirms that 8 out of 8 synthetic scenarios define their ground-truth labels "
        "using behavioral attributes that directly map to one or more canonical features in the "
        "pre-confirmation feature space."
    )

    limitation_statement = (
        "CRITICAL BENCHMARK LIMITATION: In this synthetic proof-of-concept environment, "
        "the machine learning models and heuristic rules are effectively learning the parameter "
        "boundaries established by the synthetic transaction generator (e.g. gas multiplier thresholds, "
        "burst frequencies, calldata presence). High precision and recall scores on this local dataset "
        "reflect effective detection of these specific synthetic archetypes, but DO NOT provide evidence "
        "of real-world fraud detection efficacy on live public mainnets where adversarial strategies "
        "are obfuscated, multi-hop, and dynamically priced."
    )

    return LabelLeakageAuditReport(
        scenarios_audited=len(dependencies),
        direct_dependency_count=len(dependencies),
        scenario_dependencies=dependencies,
        summary_findings=summary,
        benchmark_limitation_statement=limitation_statement,
    )
