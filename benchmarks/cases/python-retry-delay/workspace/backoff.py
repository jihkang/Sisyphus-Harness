def retry_delay(attempt, base_delay):
    return base_delay * attempt
