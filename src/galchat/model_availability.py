"""Stop repeated calls after actionable service failures; manual retry resets."""
import time

TERMINAL = {'insufficient_balance', 'quota_exceeded', 'authentication', 'access_denied', 'configuration', 'output_limit', 'refused'}
TRANSIENT = {'network', 'timeout', 'rate_limit', 'service_unavailable', 'service_error'}


class ModelAvailability:
    def __init__(self):
        self.failure = None

    def clear(self):
        self.failure = None

    def record(self, error, signature):
        code = getattr(error, 'code', None)
        if code in TERMINAL | TRANSIENT:
            self.failure = {'error_code': code, 'error': str(error), 'signature': signature,
                            'until': None if code in TERMINAL else time.monotonic() + 30}

    def blocked(self, signature):
        failure = self.failure
        if not failure:
            return None
        if failure['signature'] != signature or (failure['until'] is not None
                and time.monotonic() >= failure['until']):
            self.clear()
            return None
        return {'status': 'error', 'error': failure['error'], 'error_code': failure['error_code'],
                'automatic_blocked': True}
