import os
import re
import subprocess
import sys
import threading

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
		"""As stdin(), but also accepts the value subprocess.STDOUT to redirect stderr to stdout."""
		return self._copy(_stderr=value)

	def workdir(self, dir):
		"""Set working directory of command. Note it is relative to the main program's working directory,
			not any already-set working directory on the command.
		"""
		return self._copy(_workdir=dir)

	def __or__(self, other):
		"""Form a pipeline with another command or pipeline"""
		if isinstance(other, Command):
			return Pipeline((self, other))
		elif isinstance(other, Pipeline):
			return Pipeline((self,) + other._commands)
		else:
			return NotImplemented

	def run(self, error_on_failure=True):
		"""Actually execute the command. Blocks until completed.
		By default, will raise an error if the command exits non-zero.
		If error_on_failure = False, instead returns a subprocess.CompletedProcess.
		"""
		proc = self._run()
		if error_on_failure:
			proc.check_returncode()
			return
		else:
			return proc

	def get_output(self, error_on_failure=True):
		"""Like run(), executes the command. Unlike run, returns stdout as a byte string.
		*Note this overrides any configured stdout behaviour*.
		By default, will raise an error if the command exits non-zero.
		If error_on_failure = False, instead returns subprocess.CompletedProcess.
		"""
		proc = self.stdout(subprocess.PIPE)._run()
		if error_on_failure:
			proc.check_returncode()
			return proc.stdout
		else:
			return proc

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
		return subprocess.CompletedProcess(self._args, retcode, stdout, stderr)

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
				value = subprocess.DEVNULL,
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


class Pipeline:
	"""Represents a list of commands, where each one's stdin is connected to the 
	previous command's stdout.
	The intended way to construct this is to use the | operator on two commands:
		(cmd("ls") | cmd("wc", "-l")).run()
	"""
	def __init__(self, commands):
		self._commands = tuple(commands)
		if not self._commands:
			raise ValueError("Command list cannot be empty")

	def __repr__(self):
		return " | ".join(map(repr, self._commands))

	def env(self, **env):
		"""Set or update environment variables in all commands in the pipeline."""
		return Pipeline(command.env(**env) for command in self._commands)

	def stdin(self, value):
		"""As Command.stdin(), applies to first command"""
		return Pipeline((self._commands[0].stdin(value),) + self._commands[1:])

	def stdin_data(self, data):
		"""As Command.stdin_data(), applies to first command"""
		return Pipeline((self._commands[0].stdin_data(data),) + self._commands[1:])

	def stdout(self, value):
		"""As Command.stdout(), applies to last command"""
		return Pipeline(self._commands[:-1] + (self._commands[-1].stdout(value),))

	def stderr(self, value):
		"""As Command.stderr(), applies to all commands"""
		return Pipeline(command.stderr(value) for command in self._commands)

	def workdir(self, dir):
		"""As Command.workdir(), applies to all commands"""
		return Pipeline(command.workdir(dir) for command in self._commands)

	def __or__(self, other):
		"""Add another command or pipeline onto this pipeline"""
		if isinstance(other, Command):
			return Pipeline(self._commands + (other,))
		elif isinstance(other, Pipeline):
			return Pipeline((self._commands + other._commands))
		else:
			return NotImplemented

	def run(self, error_on_failure="last"):
		"""Actually execute the pipeline. Blocks until completed.
		The behaviour if a command fails is determined by the error_on_failure value:
			"last": Default. Will raise an error only if the last command exits non-zero.
			"any": Will error if any of the commands fail. Equivalent to "set -o pipefail" in bash.
			"never": Will not error on failure. A list of subprocess.CompletedProcess is returned.
		"""
		procs = self._run(error_on_failure)
		return procs if error_on_failure == "never" else None

	def get_output(self, error_on_failure="last"):
		"""As run(), but captures stdout of the last command and returns it.
		*Note this overrides any configured stdout behaviour*.
		If error_on_failure = "never", returns a list of subprocess.CompletedProcess.
			In this case you can access the stdout via `result[-1].stdout`.
		"""
		procs = self.stdout(subprocess.PIPE)._run(error_on_failure)
		return procs if error_on_failure == "never" else procs[-1].stdout

	def run_nonblocking(self):
		"""Executes the pipeline, but instead of blocking until completed, it returns immediately,
		returning a list of subprocess.Popen objects.
		It is an error to combine this with writing string data to stdin as this requires blocking
		on the command reading the stdin data.
		"""
		procs = []
		for i, command in enumerate(self._commands):
			if i > 0: # all but first, take stdin from previous
				command = command.stdin(procs[-1].stdout)
			if i < len(self._commands) - 1: # all but last, stdout is a pipe
				command = command.stdout(subprocess.PIPE)
			procs.append(command.run_nonblocking())
		return procs

	def _run(self, error_on_failure):
		"""Common code for blocking execution methods"""
		procs = []
		completed = []
		stdin = self._commands[0]._stdin
		input_data = stdin[1] if stdin[0] == "data" else None

		try:
			procs = self.run_nonblocking()

			# We need to potentially simultaneously:
			# - Write stdin data
			# - Read stdout data
			# - Read stderr data (not officially supported but nice to have)
			# Popen.communicate() does this with threads, so that's probably the safest choice
			# to also do here.

			# Thread results go here. stderrs have the proc as a key, stdout is "stdout"
			bufs = {}
			def read_to_buf(file, key):
				bufs[key] = file.read()
				file.close()

			# Make one thread per stderr, plus one for stdout
			def make_thread(*args):
				thread = threading.Thread(target=read_to_buf, args=args)
				thread.daemon = True
				thread.start()
				return thread
			threads = [
				make_thread(proc.stderr, proc)
				for proc in procs if proc.stderr
			]
			if procs[-1].stdout:
				threads.append(make_thread(procs[-1].stdout, "stdout"))

			# Write stdin on the main thread
			if input_data is not None:
				try:
					procs[0].stdin.write(input_data)
					procs[0].stdin.close()
				except BrokenPipeError:
					pass

			# Block until stdouts/errs close first. This mimics Popen.communicate().
			# Technically we could get a hang here if the child hands off the pipe to someone
			# else then exits, but this is the same behaviour as the stdlib.
			for thread in threads:
				thread.join()

			# Now block until processes finish and assemble the output.
			completed = []
			for proc in procs:
				retcode = proc.wait()
				stdout = bufs.get("stdout") if proc is procs[-1] else None
				stderr = bufs.get(proc)
				completed.append(subprocess.CompletedProcess(proc.args, retcode, stdout, stderr))
		except BaseException:
			# If we bail out at any point above, kill all the commands before returning
			for proc in procs:
				if proc.poll() is None:
					try:
						proc.kill()
					except ProcessLookupError:
						# We just checked if it's running, but there's a TOCTOU window here.
						# Ignore errors if it turns out to not be running after all.
						pass
			raise

		# Check for errors
		if error_on_failure == "any":
			for proc in completed:
				proc.check_returncode()
		elif error_on_failure == "last":
			completed[-1].check_returncode()
		return completed


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

def find(path, *args):
	"""Executes a `find(1)` command in the given directory with the given args used as test
	predicates. The matching filenames are returned as a list. Example:
		find(".", "-type", "f", "-name" "*.txt")
	"""
	if len(args) > 0:
		args = ("(",) + args + (")",)
	return cmd("find", path, *args, "-print0").get_output().split("\0")

def match_files(regex, path="."):
	"""Returns a list of all files under the given path that match the given regex."""
	regex = re.compile(f"^({regex})$")
	results = []
	for dirpath, dirnames, filenames in os.walk(path):
		for name in dirnames + filenames:
			fullname = os.path.join(dirpath, name)
			if regex.match(fullname):
				results.append(fullname)
	return results

def write(path, contents, newline=True):
	"""Write file contents to given path, by default with a trailing newline"""
	if isinstance(contents, str):
		contents = contents.encode()
	with open(path, "wb") as f:
		f.write(contents)
		if newline:
			f.write(b"\n")
