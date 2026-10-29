local settings_path = os.getenv("QFW_SLURM_BB_CONFIG") or
	"/etc/openqse/qfw-slurm/burst-buffer.lua.conf"
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
	settings.plugin_config or "/etc/openqse/qfw-slurm/plugin.conf"
local state_dir = os.getenv("QFW_SLURM_STATE_DIR") or settings.state_dir or
	"/var/lib/qfw-slurm/allocations"
local scancel = os.getenv("QFW_SLURM_SCANCEL") or settings.scancel or
	"/usr/bin/scancel"
local scontrol = os.getenv("QFW_SLURM_SCONTROL") or settings.scontrol or
	"/usr/bin/scontrol"
local max_reservation_attempts = settings.max_reservation_attempts or 8
local reservation_retries = tonumber(settings.reservation_retries) or 3
local reservation_retry_delay = tonumber(settings.reservation_retry_delay) or 2
local reservation_retry_budget = tonumber(settings.reservation_retry_budget) or
	60
local reservation_wait_interval =
	tonumber(settings.reservation_wait_interval) or 15
local reservation_wait_budget =
	tonumber(settings.reservation_wait_budget) or 90

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
	job_info)
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
	return table.concat(command, " ")
end

local function run_helper(operation, job_id, job_script, uid, gid,
	job_info)
	local command, message = helper_command(operation, job_id, job_script,
		uid, gid, job_info)
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
	os.execute(quote(scancel) .. " --quiet " .. tostring(job_id))
end

local function log_info(message)
	if slurm.log_info ~= nil then
		slurm.log_info("%s", message)
	end
end

local function pause(seconds)
	if seconds > 0 then
		os.execute("sleep " .. quote(seconds))
	end
end

-- Run the final reservation again until the QPM accepts or rejects it.
-- A pre-run failure makes Slurm tear the job down and requeue it, and
-- Slurm 25.05 never schedules that requeued job again (see fail_job), so a
-- condition that is expected to clear must be waited out here, with the
-- nodes assigned, instead of being reported to Slurm. That is a transient
-- helper failure (30, or a helper that could not be run), retried with a
-- pause that doubles, and a QPM that reports a delay (10) because its
-- capacity changed after the evaluation, reserved again at a fixed
-- interval. A rejection (20) is a decision and ends the loop at once. The
-- helper's own attempt limit bounds the delayed attempts as well, and both
-- time budgets count from the first attempt.
local function reserve(job_id, job_script, uid, gid, job_info)
	local retries = math.max(0, math.floor(reservation_retries))
	local retry_delay = math.max(0, reservation_retry_delay)
	local started = os.time()
	local attempt = 1
	local retried = 0
	local status, message = run_helper("reserve", job_id, job_script, uid,
		gid, job_info)
	while status ~= 0 and status ~= 20 do
		local elapsed = os.time() - started
		local pause_seconds
		if status == 10 then
			if reservation_wait_interval <= 0 or
				elapsed + reservation_wait_interval >
				reservation_wait_budget then
				break
			end
			pause_seconds = reservation_wait_interval
			log_info(string.format("slurm_bb_pre_run: job %s: QPM " ..
				"reservation attempt %d delayed (%s), waiting %s s",
				tostring(job_id), attempt, message,
				tostring(pause_seconds)))
		else
			if retried >= retries or
				elapsed + retry_delay > reservation_retry_budget then
				break
			end
			retried = retried + 1
			pause_seconds = retry_delay
			retry_delay = retry_delay * 2
			log_info(string.format("slurm_bb_pre_run: job %s: QPM " ..
				"reservation attempt %d failed (%s), retry %d of %d " ..
				"in %s s", tostring(job_id), attempt, message, retried,
				retries, tostring(pause_seconds)))
		end
		pause(pause_seconds)
		attempt = attempt + 1
		status, message = run_helper("reserve", job_id, job_script, uid,
			gid, job_info)
	end
	return status, message, attempt
end

-- Cancel the job before reporting a pre-run failure to Slurm. On Slurm
-- 25.05 the burst_buffer/lua plugin answers a pre-run error by requeueing
-- the job with the state reason FAIL_BURST_BUFFER_OP, and slurmctld's
-- job_independent() treats that reason as a hold: the job keeps its
-- priority, is not reported as held and is never scheduled again until an
-- operator runs scontrol release. A cancelled job is a visible failure.
--
-- The reason goes into the job's AdminComment first. Slurm ends the running
-- pre-run hook as part of the cancel, so the error message returned below
-- never reaches the job record and the job would only show "CANCELLED by
-- <SlurmUser>". scontrol show job and sacct report the comment, and
-- accounting keeps it without any AccountingStoreFlags setting.
local function comment_job(job_id, message)
	if not string.match(tostring(job_id), "^[0-9]+$") then
		return
	end
	os.execute(quote(scontrol) .. " update JobId=" .. tostring(job_id) ..
		" " .. quote("AdminComment=slurm_bb_pre_run: " .. message))
end

local function fail_job(job_id, message)
	log_info(string.format("slurm_bb_pre_run: job %s: cancelling: %s",
		tostring(job_id), message))
	comment_job(job_id, message)
	cancel_job(job_id)
	return slurm.ERROR, message
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
	-- Slurm requires this callback. Remote SPANK retrieves accepted context
	-- directly from the authenticated gateway after pre-run.
	return slurm.SUCCESS
end

function slurm_bb_pre_run(job_id, job_script, uid, gid, job_info)
	local status, message, attempts = reserve(job_id, job_script, uid, gid,
		job_info)
	if status == 0 then
		return slurm.SUCCESS
	end
	local count = attempts == 1 and "1 attempt" or
		tostring(attempts) .. " attempts"
	if status == 20 and attempts == 1 then
		return fail_job(job_id, "QPM reservation rejected: " .. message)
	end
	if status == 20 then
		return fail_job(job_id, "QPM reservation rejected after " .. count ..
			": " .. message)
	end
	if status == 10 then
		return fail_job(job_id, "QPM reservation still delayed after " ..
			count .. ": " .. message)
	end
	return fail_job(job_id, "QPM reservation failed after " .. count .. ": " ..
		message)
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
	return {
		helper_command = helper_command,
		run_helper = run_helper,
		cancel_job = cancel_job,
	}
end
