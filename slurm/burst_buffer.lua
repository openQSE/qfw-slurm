local settings_path = os.getenv("QFW_SLURM_BB_CONFIG") or
	"/etc/qfw-slurm/burst-buffer.lua.conf"
local settings_stream = io.open(settings_path, "r")
local settings = {}
if settings_stream ~= nil then
	settings_stream:close()
	settings = dofile(settings_path)
end
local helper = os.getenv("QFW_SLURM_BB_HELPER") or settings.helper or
	"/usr/libexec/qfw-slurm/qfw-slurm-bb"
local driver = os.getenv("QFW_SLURM_DRIVER") or settings.driver or
	"/usr/bin/qfw-slurm-driver"
local plugin_config = os.getenv("QFW_SLURM_PLUGIN_CONFIG") or
	settings.plugin_config or "/etc/qfw-slurm/plugin.conf"
local state_dir = os.getenv("QFW_SLURM_STATE_DIR") or settings.state_dir or
	"/var/lib/qfw-slurm/allocations"
local max_reservation_attempts = settings.max_reservation_attempts or 8

local function quote(value)
	value = tostring(value)
	return "'" .. string.gsub(value, "'", "'\\''") .. "'"
end

local function canonical_job_id(job_id, job_info)
	local hetero = tonumber(job_info.het_job_id) or 0
	if hetero ~= 0 and hetero ~= 4294967294 and hetero ~= 4294967295 then
		return hetero
	end
	return tonumber(job_id)
end

local function helper_command(operation, job_id, job_script, uid, gid,
	job_info, path_file)
	local cluster = job_info.cluster
	local submit_time = tonumber(job_info.submit_time)
	local restart_count = tonumber(job_info.restart_cnt) or 0
	local time_limit = tonumber(job_info.time_limit)
	if cluster == nil or not string.match(cluster, "^[A-Za-z0-9_.-]+$") or
		submit_time == nil or time_limit == nil or time_limit <= 0 then
		return nil, "invalid authoritative Slurm job metadata"
	end
	local command = {
		quote(helper), quote(operation), "--driver", quote(driver),
		"--plugin-config", quote(plugin_config), "--state-dir",
		quote(state_dir), "--cluster", quote(cluster), "--job-id",
		quote(job_id), "--canonical-job-id",
		quote(canonical_job_id(job_id, job_info)), "--uid", quote(uid),
		"--gid", quote(gid), "--submit-time", quote(submit_time),
		"--restart-count", quote(restart_count), "--walltime-seconds",
		quote(time_limit * 60), "--max-reservation-attempts",
		quote(max_reservation_attempts),
	}
	local hetero = tonumber(job_info.het_job_id) or 0
	if hetero ~= 0 and hetero ~= 4294967294 and hetero ~= 4294967295 then
		table.insert(command, "--het-job-id")
		table.insert(command, quote(hetero))
		table.insert(command, "--het-component")
		table.insert(command, quote(job_info.het_job_offset or 0))
	end
	if job_script ~= nil then
		table.insert(command, "--job-script")
		table.insert(command, quote(job_script))
	end
	if path_file ~= nil then
		table.insert(command, "--path-file")
		table.insert(command, quote(path_file))
	end
	return table.concat(command, " ")
end

local function run_helper(operation, job_id, job_script, uid, gid,
	job_info, path_file)
	local command, message = helper_command(operation, job_id, job_script,
		uid, gid, job_info, path_file)
	if command == nil then
		return 30, message
	end
	local status_path = state_dir .. "/.status-" .. tostring(job_id) ..
		"-" .. operation
	os.execute(command .. "; qfw_status=$?; printf '%s\\n' " ..
		"\"${qfw_status}\" > " .. quote(status_path))
	local stream = io.open(status_path, "r")
	if stream == nil then
		return 30, "qfw-slurm helper status is unavailable"
	end
	local status = tonumber(stream:read("*line"))
	stream:close()
	os.remove(status_path)
	if status == 0 then
		return 0, ""
	end
	if status == nil then
		return 30, "qfw-slurm helper status is invalid"
	end
	return status, "qfw-slurm helper exited with status " .. tostring(status)
end

local function cancel_job(job_id)
	if not string.match(tostring(job_id), "^[0-9]+$") then
		return
	end
	os.execute("/usr/bin/scancel --quiet " .. tostring(job_id))
end

function slurm_bb_job_process(job_script, uid, gid, job_info)
	local stream = io.open(job_script, "r")
	if stream == nil then
		return slurm.ERROR, "cannot read QFW allocation directive"
	end
	local directive = nil
	for line in stream:lines() do
		if string.find(line, "#QFW ", 1, true) == 1 then
			if directive ~= nil then
				stream:close()
				return slurm.ERROR, "multiple QFW allocation directives"
			end
			directive = line
		end
	end
	stream:close()
	if directive == nil or #directive > 2048 or
		not string.find(directive, "#QFW v=1 ", 1, true) then
		return slurm.ERROR, "invalid QFW allocation directive"
	end
	return slurm.SUCCESS, directive .. "\n"
end

function slurm_bb_pools()
	return slurm.SUCCESS
end

function slurm_bb_setup(job_id, uid, gid, pool, bb_size, job_script,
	job_info)
	return slurm.SUCCESS
end

function slurm_bb_data_in(job_id, job_script, uid, gid, job_info)
	return slurm.SUCCESS
end

function slurm_bb_test_data_in(job_id, job_script, uid, gid, job_info)
	local status, message = run_helper("evaluate", job_id, job_script,
		uid, gid, job_info)
	if status == 0 then
		return slurm.SUCCESS
	end
	if status == 10 or status == 30 then
		return slurm.SUCCESS, slurm.SLURM_BB_BUSY
	end
	if status == 20 then
		cancel_job(job_id)
		return slurm.ERROR, "QPM evaluation rejected: " .. message
	end
	return slurm.ERROR, message
end

function slurm_bb_real_size(job_id, uid, gid, job_info)
	return slurm.SUCCESS
end

function slurm_bb_paths(job_id, job_script, path_file, uid, gid, job_info)
	-- Slurm 25.05 imports this file before pre-run. The remote SPANK path
	-- injects accepted context without creating or modifying a reservation.
	return slurm.SUCCESS
end

function slurm_bb_pre_run(job_id, job_script, uid, gid, job_info)
	local status, message = run_helper("reserve", job_id, job_script,
		uid, gid, job_info)
	if status == 0 then
		return slurm.SUCCESS
	end
	if status == 20 then
		cancel_job(job_id)
		return slurm.ERROR, "QPM reservation rejected: " .. message
	end
	return slurm.ERROR, "QPM reservation retry required: " .. message
end

function slurm_bb_post_run(job_id, job_script, uid, gid, job_info)
	return slurm.SUCCESS
end

function slurm_bb_data_out(job_id, job_script, uid, gid, job_info)
	return slurm.SUCCESS
end

function slurm_bb_test_data_out(job_id, job_script, uid, gid, job_info)
	return slurm.SUCCESS
end

function slurm_bb_job_teardown(job_id, job_script, hurry, uid, gid)
	local fake_info = {
		cluster = settings.cluster or "auto",
		submit_time = 1,
		restart_cnt = 0,
		time_limit = 1,
		het_job_id = 0,
	}
	run_helper("release", job_id, nil, uid, gid, fake_info)
	return slurm.SUCCESS
end

function slurm_bb_get_status(uid, gid, ...)
	return slurm.SUCCESS, "qfw-slurm burst-buffer provider is available\n"
end

if os.getenv("QFW_SLURM_LUA_TEST") == "1" then
	return {helper_command = helper_command, run_helper = run_helper}
end
