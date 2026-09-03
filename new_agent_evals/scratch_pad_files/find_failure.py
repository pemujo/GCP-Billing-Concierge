import json
import sys
import glob
import os

pattern = "/usr/local/google/home/sergiovillani/code/work/gcp_billing_concierge_agent/GCP_billing_concierge/.adk/eval_history/GCP_billing_concierge_billing_eval_dataset_fully_modern_1776726375*.evalset_result.json"

files = glob.glob(pattern)
print(f"Found {len(files)} files matching pattern.")

failed_cases = []
for file_path in files:
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            for case in data.get('eval_case_results', []):
                eval_id = case.get('eval_id')
                status = case.get('final_eval_status')
                if status != 1:
                    failed_cases.append((eval_id, status, os.path.basename(file_path)))
    except Exception as e:
        print(f"Error loading file {file_path}: {e}")

print(f"Failed cases: {failed_cases}")
