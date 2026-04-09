import json
import re
import subprocess


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


def get_context(prompt):
    table = "`[your-project-id].billing_concierge_eval_dataset.billing_sample_table`"

    if "total cost recorded in the dataset" in prompt:
        sql = f"SELECT SUM(cost) as total_cost FROM {table}"
        res = run_query(sql)
        if res:
            return f"The total cost recorded in the dataset is {res[0]['total_cost']} USD."

    elif "top spending service" in prompt:
        sql = f"SELECT service.description as service_desc, SUM(cost) as total_cost FROM {table} GROUP BY service_desc ORDER BY total_cost DESC LIMIT 1"
        res = run_query(sql)
        if res:
            return f"The top spending service is {res[0]['service_desc']} with a cost of {res[0]['total_cost']} USD."

    elif "Which project had the highest spend" in prompt:
        sql = f"SELECT project.name as project_name, SUM(cost) as total_cost FROM {table} GROUP BY project_name ORDER BY total_cost DESC LIMIT 1"
        res = run_query(sql)
        if res:
            return f"The project with the highest spend was {res[0]['project_name']} with a cost of {res[0]['total_cost']} USD."

    match = re.search(r"How much did (.*) cost\?", prompt)
    if match and "project" not in prompt:
        service = match.group(1)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE service.description = '{service}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return f"The cost of {service} was {res[0]['total_cost']} USD."

    match = re.search(r"How much did the (.*) project cost\?", prompt)
    if match:
        project = match.group(1)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE project.name = '{project}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return f"The {project} project cost {res[0]['total_cost']} USD."

    match = re.search(
        r"How much did the SKU '(.*)' cost\?", prompt
    )
    if match:
        sku = match.group(1)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE sku.description = '{sku}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return f"The SKU '{sku}' cost {res[0]['total_cost']} USD."

    match = re.search(r"What was the total cost on (.*)\?", prompt)
    if match:
        date = match.group(1)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE DATE(usage_start_time) = '{date}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return (
                f"The total cost on {date} was {res[0]['total_cost']} USD."
            )

    match = re.search(r"What was the spend for project '(.*)'\?", prompt)
    if match:
        project = match.group(1)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE project.name = '{project}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return f"The spend for project '{project}' was {res[0]['total_cost']} USD."

    match = re.search(
        r"How much did (.*) cost in project '(.*)'\?", prompt
    )
    if match:
        service = match.group(1)
        project = match.group(2)
        sql = f"SELECT SUM(cost) as total_cost FROM {table} WHERE service.description = '{service}' AND project.name = '{project}'"
        res = run_query(sql)
        if res and res[0]["total_cost"] is not None:
            return f"In project '{project}', {service} cost {res[0]['total_cost']} USD."

    return "Context not found."


def main():
    input_file = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset.json"
    output_file = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset_with_context.json"

    with open(input_file, "r") as f:
        dataset = json.load(f)

    for item in dataset:
        prompt = item["prompt"]
        context = get_context(prompt)
        item["context"] = context

    with open(output_file, "w") as f:
        json.dump(dataset, f, indent=2)

    print(f"Saved updated dataset to {output_file}")


if __name__ == "__main__":
    main()
