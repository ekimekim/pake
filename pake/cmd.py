import subprocess
import sys
import os

"""Provides a builder for running child processes in an ergonomic way."""

class Command:
	"""
	A builder for options when running child processes.
	Immutable, all methods return a new Command.

	Methods are additive, so you can define command "stems". eg:
		sudo = Command("sudo")
		install = sudo("apt-get", "install")
		install("pake").run()
	"""
	_COPY_ATTRS = ["_args", "_env", "_stdin", "_stdout", "_stderr", "_workdir"]

	def __init__(self):
		self._args = ()
		self._env = {}
		self._stdin = ("file", sys.stdin) # ("file", fileobj | string) | ("data", string)
		self._stdout = sys.stdout # fileobj | string
		self._stderr = sys.stderr # fileobj | string
		self._workdir = "."

	def __repr__(self):
		return f"<Command {self._args} {self._env}>"

	__str__ = __repr__

	def _copy(self, **updates):
		new = Command()
		for attr in self._COPY_ATTRS:
			setattr(new, attr, updates.get(attr, getattr(self, attr)))
		return new

	def args(self, args):
		"""Append args onto the argument list. Args will be coerced to string."""
		return self._copy(_args = self._args + tuple(str(arg) for arg in args))

	def __call__(self, *args):
		"""Return a Command with given args. It does not run immediately,
		but can be run with .run() when ready.

		cmd(*args) is equivalent to cmd.args(args) but is intended to be ergonomic with stemming.
		"""
		return self.args(args)

	def env(self, **env):
		"""Set or update environment variables. Values will be coerced to string."""
		return self._copy(_env = self._env | env)

	def stdin(self, value):
		"""Set stdin to one of the following:
			string: Filepath to use as stdin. Note it is relative to the main program's working directory,
				not the command's working directory.
			file object: File to use as stdin. Must be a "real" open file (have a fileno).
			None: equivalent to "/dev/null".
		"""
		return self._copy(_stdin=("file", value))

	def stdin_data(self, data):
		"""Set stdin to send the given string/bytes data over a pipe."""
		return self._copy(_stdin=("data", data))

	def stdout(self, value):
		"""As stdin()"""
		return self._copy(_stdout=value)

	def stderr(self, value):
		"""As stdin()"""
		return self._copy(_stderr=value)

	def workdir(self, dir):
		"""Set working directory of command. Note it is relative to the main program's working directory,
			not any already-set working directory on the command.
		"""
		return self._copy(_workdir=dir)

	def run(self, error_on_failure=True):
		"""Actually execute the command. Blocks until completed.
		By default, will raise an error if the command exits non-zero.
		If error_on_failure = False, instead returns the exit code.
		"""
		retcode, stdout, stderr = self._run()
		if error_on_failure:
			return
		else:
			return retcode

	def get_output(self, error_on_failure=True):
		"""Like run(), executes the command. Unlike run, returns stdout as a byte string.
		*Note this overrides any configured stdout behaviour*.
		By default, will raise an error if the command exits non-zero.
		If error_on_failure = False, instead returns (exit code, output).
		"""
		retcode, stdout, stderr = self.stdout(subprocess.PIPE)._run()
		if error_on_failure:
			return stdout
		else:
			return retcode, stdout

	def run_nonblocking(self):
		"""Executes the command, but instead of blocking until completed, it returns immediately,
		returning the subprocess.Popen instance.
		It is an error to combine this with writing string data to stdin as this requires blocking
		on the command reading the stdin data."""
		if self._stdin[0] == "data":
			raise ValueError("Cannot use run_nonblocking() with stdin data")
		return self._make_proc()

	def _run(self):
		"""Common code for blocking execution methods"""
		proc = None
		input_data = self._stdin[1] if self._stdin[0] == "data" else None
		if isinstance(input_data, str):
			input_data = input_data.encode()
		try:
			proc = self._make_proc()
			stdout, stderr = proc.communicate(input_data)
			retcode = proc.wait()
		except BaseException:
			# attempt to kill before returning
			try:
				if proc is not None:
					proc.kill()
			except ProcessLookupError:
				pass # process not existing is fine, ignore it.
			raise
		return retcode, stdout, stderr

	def _make_proc(self):
		"""Common code for creating the Popen object"""
		if self._stdin[0] == "data":
			stdin = subprocess.PIPE
		elif self._stdin[0] == "file":
			stdin = self._stdin[1]
		else:
			raise AssertionError(f"Bad stdin value: {self._stdin}")

		def to_file(value, mode):
			if value is None:
				value = "/dev/null"
			if isinstance(value, str):
				value = value.encode()
			if isinstance(value, bytes):
				value = open(value, mode)
			return value

		return subprocess.Popen(
			self._args,
			env = os.environ | self._env,
			close_fds = True,
			stdin = to_file(stdin, "rb"),
			stdout = to_file(self._stdout, "wb"),
			stderr = to_file(self._stderr, "wb"),
			cwd = self._workdir,
		)


# Root instance from which copies get made
cmd = Command()

sudo = cmd("sudo")

def run(*args):
	"""Shortcut for cmd(*args).run()"""
	cmd(*args).run()

def shell(command, **env):
	"""Creates a Command to run given command string in your $SHELL.
	It is highly recommended that any dynamic values are passed in as environment variables
	as this is the easiest way to safely escape the values. For this reason, keyword args to this
	function are passed as env vars. For example, this is not safe:
		shell(f'rm "foo/{path}"').run()
	and will fail if path contains double quotes, eg. `"; rm -rf /; : "`.
	This is much safer:
		shell('rm "foo/$PATH"', PATH=path).run()
	"""
	return cmd(os.environ["SHELL"], "-c", command).env(**env)
