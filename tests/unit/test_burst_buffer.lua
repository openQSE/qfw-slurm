local source = arg[1]
local work = arg[2]
local helper = work .. "/helper"
local calls = work .. "/calls"
local statuses = work .. "/statuses"
local settings = work .. "/burst-buffer.lua.conf"
local logged = {}

os.execute("mkdir -p " .. work .. "/state")

slurm = {
	SUCCESS = 0,
	ERROR = 1,
	SLURM_BB_BUSY = "BUSY",
	log_info = function(format, ...)
		table.insert(logged, string.format(format, ...))
	end,
}

-- Load the provider with the given burst-buffer.lua.conf body. Paths come
-- from the environment, as they do under CTest.
local function load_module(settings_body)
	local settings_stream = assert(io.open(settings, "w"))
	settings_stream:write(settings_body)
	settings_stream:close()
	local original_getenv = os.getenv
	os.getenv = function(name)
		local values = {
			QFW_SLURM_BB_CONFIG = settings,
			QFW_SLURM_BB_HELPER = helper,
			QFW_SLURM_DRIVER = "/test/driver",
			QFW_SLURM_PLUGIN_CONFIG = "/test/plugin.conf",
			QFW_SLURM_STATE_DIR = work .. "/state",
			QFW_SLURM_SCANCEL = work .. "/scancel",
			QFW_SLURM_LUA_TEST = "1",
		}
		return values[name] or original_getenv(name)
	end
	local loaded = dofile(source)
	os.getenv = original_getenv
	return loaded
end

-- Short pauses keep the suite fast while the retry path still sleeps.
local module = load_module("return {reservation_retry_delay = 0.2}\n")

-- The fake helper records each operation and exits with the next planned
-- status, or 0 when nothing is planned.
local stream = assert(io.open(helper, "w"))
stream:write("#!/bin/sh\necho \"$1\" >>", calls, "\n",
	"status=0\n",
	"if [ -s ", statuses, " ]; then\n",
	"\tstatus=$(head -n 1 ", statuses, ")\n",
	"\ttail -n +2 ", statuses, " >", statuses, ".next\n",
	"\tmv ", statuses, ".next ", statuses, "\n",
	"fi\n",
	"exit \"${status}\"\n")
stream:close()
os.execute("chmod 755 " .. helper)

local scancel_stream = assert(io.open(work .. "/scancel", "w"))
scancel_stream:write("#!/bin/sh\necho cancel:$2 >>", calls, "\n")
scancel_stream:close()
os.execute("chmod 755 " .. work .. "/scancel")

local function plan_statuses(values)
	local plan = assert(io.open(statuses, "w"))
	for _, value in ipairs(values) do
		plan:write(value, "\n")
	end
	plan:close()
end

local function reset_calls()
	local reset = assert(io.open(calls, "w"))
	reset:close()
	logged = {}
end

local function read_calls()
	local reader = assert(io.open(calls, "r"))
	local content = reader:read("*all")
	reader:close()
	return content
end

local info = {
	cluster = "test-cluster",
	submit_time = 1788000000,
	restart_cnt = 0,
	time_limit = 60,
	het_job_id = 0,
}
local status = module.run_helper("evaluate", 41, "/tmp/script", 1001,
	1001, info)
assert(status == 0)
assert(read_calls() == "evaluate\n")

local command = assert(module.helper_command("reserve", 42, "/tmp/script",
	1001, 1001, {
		cluster = "test-cluster",
		submit_time = 1788000000,
		restart_cnt = 2,
		time_limit = 10,
		het_job_id = 40,
		het_job_offset = 1,
	}))
assert(string.find(command, "--canonical-job-id '40'", 1, true))
assert(string.find(command, "--het-component '1'", 1, true))

local job_script = work .. "/job-script"
stream = assert(io.open(job_script, "w"))
stream:write("#!/bin/sh\n")
for index = 1, 300 do
	stream:write("# ordinary application comment ", index, "\n")
end
stream:write("#QFW v=1 qpu=nwqsim-site workload=quantum circuits=1 ",
	"qubits=5 depth=20 shots=16\n")
stream:close()
local process_status, directive = slurm_bb_job_process(job_script, 1001,
	1001, info)
assert(process_status == slurm.SUCCESS)
assert(directive == "#QFW v=1 qpu=nwqsim-site workload=quantum " ..
	"circuits=1 qubits=5 depth=20 shots=16\n")

module.cancel_job(43)
assert(string.find(read_calls(), "cancel:43", 1, true))

-- Pre-run retries a transient reservation failure and succeeds on the
-- second attempt.
reset_calls()
plan_statuses({30, 0})
local pre_run_status, pre_run_message = slurm_bb_pre_run(44, job_script,
	1001, 1001, info)
assert(pre_run_status == slurm.SUCCESS, pre_run_message)
assert(read_calls() == "reserve\nreserve\n")
assert(#logged == 1)
assert(string.find(logged[1], "job 44: QPM reservation attempt 1 of 4", 1,
	true))
assert(string.find(logged[1], "status 30", 1, true))
assert(string.find(logged[1], "retrying in 0.2 s", 1, true))

-- A helper that could not be run is retried the same way.
reset_calls()
plan_statuses({127, 0})
pre_run_status, pre_run_message = slurm_bb_pre_run(44, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.SUCCESS, pre_run_message)
assert(read_calls() == "reserve\nreserve\n")
assert(#logged == 1)

-- Exhausting the retries fails pre-run with the attempt count after the
-- pauses (0.2, 0.4 and 0.8 s) and does not cancel the job.
reset_calls()
plan_statuses({30, 30, 30, 30})
local started = os.time()
pre_run_status, pre_run_message = slurm_bb_pre_run(45, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.ERROR)
assert(os.time() - started >= 1)
assert(string.find(pre_run_message, "QPM reservation failed after 4 " ..
	"attempts: ", 1, true) == 1, pre_run_message)
assert(string.find(pre_run_message, "status 30", 1, true))
assert(read_calls() == string.rep("reserve\n", 4))
assert(#logged == 3)
assert(string.find(logged[3], "attempt 3 of 4", 1, true))
assert(string.find(logged[3], "retrying in 0.8 s", 1, true))

-- A permanent rejection is not retried and cancels the job.
reset_calls()
plan_statuses({20})
pre_run_status, pre_run_message = slurm_bb_pre_run(46, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.ERROR)
assert(string.find(pre_run_message, "QPM reservation rejected: ", 1,
	true) == 1, pre_run_message)
assert(read_calls() == "reserve\ncancel:46\n")
assert(#logged == 0)

-- A delayed reservation goes back to Slurm's requeue path without a retry.
reset_calls()
plan_statuses({10})
pre_run_status, pre_run_message = slurm_bb_pre_run(47, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.ERROR)
assert(string.find(pre_run_message, "QPM reservation retry required: ", 1,
	true) == 1, pre_run_message)
assert(read_calls() == "reserve\n")
assert(#logged == 0)

-- With no retries configured pre-run makes a single attempt.
load_module("return {reservation_retries = 0}\n")
reset_calls()
plan_statuses({30, 0})
pre_run_status, pre_run_message = slurm_bb_pre_run(48, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.ERROR)
assert(string.find(pre_run_message, "QPM reservation failed after 1 " ..
	"attempt: ", 1, true) == 1, pre_run_message)
assert(read_calls() == "reserve\n")
assert(#logged == 0)

-- A retry that would overrun the time budget is not started.
load_module("return {reservation_retry_delay = 0.2, " ..
	"reservation_retry_budget = 0}\n")
reset_calls()
plan_statuses({30, 0})
pre_run_status, pre_run_message = slurm_bb_pre_run(49, job_script, 1001,
	1001, info)
assert(pre_run_status == slurm.ERROR)
assert(string.find(pre_run_message, "QPM reservation failed after 1 " ..
	"attempt: ", 1, true) == 1, pre_run_message)
assert(read_calls() == "reserve\n")
assert(#logged == 0)
