local source = arg[1]
local resources = arg[2]

slurm = {
	SUCCESS = 0,
	ERROR = 1,
	user_msg = function(message) end,
}

local function check(condition, message)
	if not condition then
		error(message)
	end
end

local prefix = "_SLURM_SPANK_OPTION_spank_quantum_"
local function environment(overrides)
	local values = {
		qpu = "nwqsim",
		workload_kind = "quantum",
		circ_count = "2",
		max_qubits = "5",
		max_depth = "120",
		max_shots = "1024",
	}
	for key, value in pairs(overrides or {}) do
		values[key] = value
	end
	local output = {}
	for key, value in pairs(values) do
		if value ~= false then
			table.insert(output, prefix .. key .. "=" .. value)
		end
	end
	return output
end

local original_getenv = os.getenv
os.getenv = function(name)
	if name == "QFW_SLURM_RESOURCES_FILE" then
		return resources
	end
	if name == "QFW_SLURM_LUA_TEST" then
		return "1"
	end
	return original_getenv(name)
end
local module = dofile(source)
os.getenv = original_getenv

local output, err = module.directive({
	spank_job_env = environment(),
	partition = "quantum",
})
check(err == nil, err)
check(output == "#QFW v=1 qpu=nwqsim-site workload=quantum " ..
	"circuits=2 qubits=5 depth=120 shots=1024", output)

output, err = module.directive({spank_job_env = nil})
check(output == "" and err == nil, "ordinary jobs must be unchanged")

output, err = module.directive({
	spank_job_env = environment({max_shots = false}),
	partition = "quantum",
})
check(output == nil and string.find(err, "max%-shots"),
	"missing fields must fail")

output, err = module.directive({
	spank_job_env = environment({max_shots = "1x"}),
	partition = "quantum",
})
check(output == nil and string.find(err, "numeric"),
	"malformed numbers must fail")

local duplicate = environment()
table.insert(duplicate, prefix .. "qpu=nwqsim")
output, err = module.directive({
	spank_job_env = duplicate,
	partition = "quantum",
})
check(output == nil and string.find(err, "duplicate"),
	"duplicates must fail")

output, err = module.directive({
	spank_job_env = environment({qpu = "missing"}),
	partition = "quantum",
})
check(output == nil and string.find(err, "unknown QPU"),
	"unknown resources must fail")
