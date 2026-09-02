local source = arg[1]
local work = arg[2]
local helper = work .. "/helper"
local calls = work .. "/calls"

slurm = {SUCCESS = 0, ERROR = 1, SLURM_BB_BUSY = "BUSY"}
local original_getenv = os.getenv
os.getenv = function(name)
	local values = {
		QFW_SLURM_BB_HELPER = helper,
		QFW_SLURM_DRIVER = "/test/driver",
		QFW_SLURM_PLUGIN_CONFIG = "/test/plugin.conf",
		QFW_SLURM_STATE_DIR = work .. "/state",
		QFW_SLURM_LUA_TEST = "1",
	}
	return values[name] or original_getenv(name)
end
local module = dofile(source)
os.getenv = original_getenv
os.execute("mkdir -p " .. work .. "/state")

local stream = assert(io.open(helper, "w"))
stream:write("#!/bin/sh\necho \"$1\" >>", calls,
	"\nexit ${QFW_TEST_STATUS:-0}\n")
stream:close()
os.execute("chmod 755 " .. helper)

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
stream = assert(io.open(calls, "r"))
assert(stream:read("*all") == "evaluate\n")
stream:close()

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
