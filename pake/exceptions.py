
class PakeError(Exception):
	"""General exception that should be reported to the user"""


class BuildError(PakeError):
	"""
	A failure while building or resolving dependencies.
	Has attached metadata indicating what target failed and the chain of targets that led to it.
	"""
	def __init__(self, target_chain, message):
		self.target_chain = target_chain
		self.message = message

	def chain_str(self):
		return " -> ".join(self.target_chain)

	def __str__(self):
		return f"{self.chain_str()}: {self.message}"


class RuleError(PakeError):
	"""This can be raised inside a recipe. It will be wrapped into a BuildError and reported
	as just the message, without a traceback. This is suitable for explicitly failing
	with an error message, eg. because some precondition does not hold."""
