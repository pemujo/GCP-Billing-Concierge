import pandas as pd
import vertexai
from vertexai import agent_engines
from vertexai.preview.evaluation import EvalTask, MetricPromptTemplateExamples
import json
import os

# Initialize Vertex AI
PROJECT_ID = "[your-project-id]"
LOCATION = "us-central1"
REMOTE_AGENT_ID = "projects/[your-project-number]/locations/us-central1/reasoningEngines/[your-engine-id]"

vertexai.init(project=PROJECT_ID, location=LOCATION)

# Define wrapper function for ADK agent
def reasoning_engine_model(prompt):
    print(f"\n--- Querying agent with: {prompt} ---")
    remote_agent = agent_engines.get(REMOTE_AGENT_ID)
    
    full_response = ""
    try:
        response_stream = remote_agent.stream_query(
            message=prompt, 
            user_id="eval-user"
        )
        
        for event in response_stream:
            if isinstance(event, dict) and 'content' in event:
                parts = event['content'].get('parts', [])
                for part in parts:
                    if 'text' in part:
                        text = part['text']
                        print(text, end="", flush=True)
                        full_response += text
            elif isinstance(event, dict) and 'actions' in event:
                # Handle actions if needed, but for now we just want the text
                pass
                
    except Exception as e:
        print(f"\nError during remote query: {e}")
        return f"Error: {e}"
        
    print("\n" + "-" * 30)
    return full_response

# Load dataset
dataset_path = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset_with_context.json"

print(f"Loading dataset from {dataset_path}")
with open(dataset_path, 'r') as f:
    data = json.load(f)

# Convert to pandas DataFrame
eval_dataset = pd.DataFrame(data)

CLARIFICATION_PROMPT = " for the entire period covered by the dataset. Please provide the exact numeric figures found in the source data without rounding."

# Execute Agent
print("Executing agent on dataset...")
responses = []
for index, row in eval_dataset.iterrows():
    response = reasoning_engine_model(row['prompt'] + CLARIFICATION_PROMPT)
    responses.append(response)

# Prepare Evaluation Data
eval_dataset['response'] = responses
# Augment prompt with context for evaluation
eval_dataset['original_prompt'] = eval_dataset['prompt']
eval_dataset['prompt'] = eval_dataset.apply(lambda row: f"Question: {row['prompt']}{CLARIFICATION_PROMPT}\nContext: {row['context']}", axis=1)

# Define Evaluation Task
eval_task = EvalTask(
    dataset=eval_dataset,
    metrics=[
        MetricPromptTemplateExamples.Pointwise.FLUENCY,
        MetricPromptTemplateExamples.Pointwise.GROUNDEDNESS,
        MetricPromptTemplateExamples.Pointwise.QUESTION_ANSWERING_QUALITY,
    ],
)

# Run evaluation
print("Starting evaluation...")
result = eval_task.evaluate()

# Print results
print("\nSummary Metrics:")
print(json.dumps(result.summary_metrics, indent=2))

print("\nDetailed Metrics Table:")
print(result.metrics_table.head())

# Save results
output_path = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/eval_results.json"
result.metrics_table.to_json(output_path, orient='records', indent=2)
print(f"\nResults saved to {output_path}")
