# cost_calculator.py

def calculate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculates the estimated cost for a given LLM interaction.
    This is a simplified example; real-world costs vary by model and provider.

    Args:
        model_id (str): The ID of the LLM model used (e.g., "gpt-4", "gpt-3.5-turbo").
        prompt_tokens (int): Number of tokens in the prompt.
        completion_tokens (int): Number of tokens in the completion.

    Returns:
        float: The estimated cost in USD.
    """
    # Example pricing (replace with actual pricing from your LLM provider)
    pricing = {
        "gpt-4": {"prompt_cost_per_token": 0.00003, "completion_cost_per_token": 0.00006},
        "gpt-3.5-turbo": {"prompt_cost_per_token": 0.0000015, "completion_cost_per_token": 0.000002},
        # Add other models as needed
    }

    model_pricing = pricing.get(model_id, pricing["gpt-3.5-turbo"]) # Default to gpt-3.5-turbo if unknown

    prompt_cost = prompt_tokens * model_pricing["prompt_cost_per_token"]
    completion_cost = completion_tokens * model_pricing["completion_cost_per_token"]

    return prompt_cost + completion_cost

def calculate_savings(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculates the estimated savings if an LLM interaction was served from cache.
    For simplicity, this currently returns the full cost that would have been incurred.

    Args:
        model_id (str): The ID of the LLM model used.
        prompt_tokens (int): Number of tokens in the prompt.
        completion_tokens (int): Number of tokens in the completion.

    Returns:
        float: The estimated savings in USD.
    """
    # Savings are typically the cost that would have been incurred if not cached.
    # In a more complex system, this might account for cache storage costs, etc.
    return calculate_cost(model_id, prompt_tokens, completion_tokens)
