local option_prefix = "_SLURM_SPANK_OPTION_spank_quantum_"
local resources_file = os.getenv("QFW_SLURM_RESOURCES_FILE") or
	"/etc/qfw-slurm/resources.lua"
local policy = dofile(resources_file)

local option_names = {
	qpu = "qpu",
	workload_kind = "workload",
	circ_count = "circuits",
	max_qubits = "qubits",
	max_depth = "depth",
	max_shots = "shots",
	max_one_q_gates = "oneq",
	max_two_q_gates = "twoq",
	max_measurements = "measurements",
}

local required = {
	"qpu", "workload_kind", "circ_count", "max_qubits", "max_depth",
	"max_shots",
}

local numeric = {
	"circ_count", "max_qubits", "max_depth", "max_shots",
	"max_one_q_gates", "max_two_q_gates", "max_measurements",
}

local canonical_order = {
	"qpu", "workload_kind", "circ_count", "max_qubits", "max_depth",
	"max_shots", "max_one_q_gates", "max_two_q_gates",
	"max_measurements",
}

local function collect_options(environment)
	local values = {}
	if environment == nil then
		return values
	end
	for _, entry in pairs(environment) do
		local key, value = string.match(entry, "^([^=]+)=(.*)$")
		if key ~= nil and string.sub(key, 1, #option_prefix) ==
			option_prefix then
			local name = string.sub(key, #option_prefix + 1)
			if option_names[name] == nil then
				return nil, "unknown qfw-slurm option metadata: " .. name
			end
			if values[name] ~= nil then
				return nil, "duplicate qfw-slurm option metadata: " .. name
			end
			values[name] = value
		end
	end
	return values
end

local function map_qpus(value, partition)
	local mapped = {}
	local seen = {}
	local partition_policy = policy.partitions and
		policy.partitions[partition] or nil
	for name in string.gmatch(value, "[^,]+") do
		if not string.match(name, "^[A-Za-z0-9_.-]+$") then
			return nil, "invalid QPU resource name"
		end
		local service_id = policy.resources[name]
		if service_id == nil then
			return nil, "unknown QPU resource: " .. name
		end
		if partition_policy and partition_policy.allowed and
			not partition_policy.allowed[name] then
			return nil, "QPU resource is not allowed in partition " ..
				partition
		end
		if seen[service_id] then
			return nil, "duplicate QPM service: " .. service_id
		end
		seen[service_id] = true
		table.insert(mapped, service_id)
	end
	if #mapped == 0 then
		return nil, "at least one QPU resource is required"
	end
	return table.concat(mapped, ",")
end

local function directive(job_desc)
	local values, err = collect_options(job_desc.spank_job_env)
	if values == nil then
		return nil, err
	end
	if next(values) == nil then
		return ""
	end
	for _, name in ipairs(required) do
		if values[name] == nil or values[name] == "" then
			return nil, "missing required option --" ..
				string.gsub(name, "_", "-")
		end
	end
	for _, name in ipairs(numeric) do
		if values[name] ~= nil and
			not string.match(values[name], "^[1-9][0-9]*$") then
			return nil, "invalid numeric option --" ..
				string.gsub(name, "_", "-")
		end
	end
	if values.workload_kind ~= "quantum" and
		values.workload_kind ~= "hybrid" then
		return nil, "invalid --workload-kind"
	end
	local services
	services, err = map_qpus(values.qpu, job_desc.partition)
	if services == nil then
		return nil, err
	end
	values.qpu = services
	local fields = {"#QFW", "v=1"}
	for _, name in ipairs(canonical_order) do
		if values[name] ~= nil then
			table.insert(fields, option_names[name] .. "=" .. values[name])
		end
	end
	local output = table.concat(fields, " ")
	if #output > 2048 then
		return nil, "QFW allocation directive exceeds 2048 bytes"
	end
	return output
end

function slurm_job_submit(job_desc, part_list, submit_uid)
	local output, err = directive(job_desc)
	if output == nil then
		slurm.user_msg("qfw-slurm: " .. err)
		return slurm.ERROR
	end
	if output ~= "" then
		job_desc.burst_buffer = output
	end
	return slurm.SUCCESS
end

function slurm_job_modify(job_desc, job_ptr, part_list, modify_uid)
	return slurm.SUCCESS
end

if os.getenv("QFW_SLURM_LUA_TEST") == "1" then
	return {directive = directive, collect_options = collect_options}
end
