import json
import re
import vertexai
from vertexai import agent_engines

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
                pass
                
    except Exception as e:
        print(f"\nError during remote query: {e}")
        return f"Error: {e}"
        
    print("\n" + "-" * 30)
    return full_response

def extract_cost(text):
    # Find patterns like $123.45 or 123.45 or 123
    # We remove commas for thousands separation if any.
    text = text.replace(",", "")
    matches = re.findall(r'[-+]?\d*\.\d+|\d+', text)
    if matches:
         return float(matches[0])
    return None

# Load dataset
dataset_path = "[your-home-directory]/.gemini/jetski/brain/[your-conversation-id]/scratch/golden_dataset_products_only.json"

print(f"Loading dataset from {dataset_path}")
with open(dataset_path, 'r') as f:
    dataset = json.load(f)

CLARIFICATION_PROMPT = " for the entire period covered by the dataset. Please round all dollar amounts to two decimal places."

passed = 0
failed = 0

for item in dataset:
    prompt = item['prompt']
    reference = item['reference']
    
    # Execute Agent
    agent_output = reasoning_engine_model(prompt + CLARIFICATION_PROMPT)
    
    expected_cost = extract_cost(reference)
    actual_cost = extract_cost(agent_output)
    
    print(f"\nPrompt: {prompt}")
    print(f"Expected (from reference): {expected_cost}")
    print(f"Actual (from response): {actual_cost}")
    
    if expected_cost is not None and actual_cost is not None:
        expected_rounded = round(expected_cost, 2)
        actual_rounded = round(actual_cost, 2)
        
        if expected_rounded == actual_rounded:
            print(f"RESULT: SUCCESS")
            passed += 1
        else:
            print(f"RESULT: FAIL (Mismatch)")
            failed += 1
    else:
        print(f"RESULT: FAIL (Extraction failed)")
        failed += 1

print(f"\n=== Custom Verification Summary ===")
print(f"Total Samples: {len(dataset)}")
print(f"Passed: {passed}")
print(f"Failed: {failed}")
