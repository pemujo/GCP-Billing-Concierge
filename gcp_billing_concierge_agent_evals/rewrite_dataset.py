import json
import re
import subprocess

PROJECT_ID = "[your-project-id]"
LOCATION = "us-central1"
TABLE = "`[your-project-id].billing_concierge_eval_dataset.billing_sample_table`"

def run_query(sql):
    print(f"Running query: {sql}")
    result = subprocess.run(
        [
            "bq",
            "query",
            "--use_legacy_sql=false",
            "--format=json",
            sql,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Error running query: {result.stderr}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Failed to parse JSON output: {result.stdout}")
        return None

def get_new_cost(service):
    sql = f"SELECT SUM(cost) as total_cost FROM {TABLE} WHERE service.description = '{service}'"
    res = run_query(sql)
    if res and res[0]["total_cost"] is not None:
        return float(res[0]["total_cost"])
    return None

def main():
    input_file = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset_with_context.json"
    output_file = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset_products_only.json"

    with open(input_file, "r") as f:
        dataset = json.load(f)

    new_dataset = []
    seen_prompts = set()

    for item in dataset:
        prompt = item["prompt"]
        
        # Pattern 1: How much did [Service] cost in project '[Project]'?
        match = re.search(r"How much did (.*) cost in project '.*'\?", prompt)
        if match:
            service = match.group(1)
            new_prompt = f"How much did {service} cost?"
            
            if new_prompt in seen_prompts:
                continue
                
            seen_prompts.add(new_prompt)
            
            # Fetch new ground truth
            total_cost = get_new_cost(service)
            if total_cost is not None:
                new_item = {
                    "prompt": new_prompt,
                    "reference": f"{service} cost approximately {round(total_cost, 2)} USD.",
                    "context": f"The cost of {service} was {total_cost} USD."
                }
                new_dataset.append(new_item)
            else:
                print(f"Failed to fetch cost for {service}")
                
        # Check if it's a general project question to drop
        elif "project" in prompt.lower() or "highest spend" in prompt.lower():
            print(f"Dropping project question: {prompt}")
            continue
            
        else:
            # Keep other questions (e.g., SKU questions, Date questions)
            if prompt not in seen_prompts:
                seen_prompts.add(prompt)
                new_dataset.append(item)

    with open(output_file, "w") as f:
        json.dump(new_dataset, f, indent=2)

    print(f"Saved updated dataset to {output_file}")
    print(f"Original size: {len(dataset)}, New size: {len(new_dataset)}")

if __name__ == "__main__":
    main()
