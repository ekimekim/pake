
class PakeError(Exception):
	"""General exception that should be reported to the user"""

class BuildError(PakeError):
	"""A failure while building or resolving dependencies"""
