#!/bin/bash

sweagent run-batch --config agent/swesmith_infer.yaml \
	--agent.model.api_base "${SWESMITH_API_BASE:-http://127.0.0.1:8000/v1}" \
	--agent.model.name "${SWESMITH_MODEL:-glm-5.3-flash}" \
	--random_delay_multiplier=1 \
	--output_dir trajectories/john-b-yang/swesmith.ablation.bug.lm_reimplement_500
