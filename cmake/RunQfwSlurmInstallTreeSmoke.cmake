if(NOT QFW_SLURM_BINARY_DIR)
	message(FATAL_ERROR "QFW_SLURM_BINARY_DIR is required")
endif()
if(NOT QFW_SLURM_INSTALL_PREFIX)
	message(FATAL_ERROR "QFW_SLURM_INSTALL_PREFIX is required")
endif()
if(NOT QFW_SLURM_INSTALL_MANDIR)
	message(FATAL_ERROR "QFW_SLURM_INSTALL_MANDIR is required")
endif()
if(NOT QFW_SLURM_INSTALL_LIBEXECDIR)
	message(FATAL_ERROR "QFW_SLURM_INSTALL_LIBEXECDIR is required")
endif()

file(REMOVE_RECURSE "${QFW_SLURM_INSTALL_PREFIX}")
execute_process(
	COMMAND "${CMAKE_COMMAND}" --install "${QFW_SLURM_BINARY_DIR}"
		--prefix "${QFW_SLURM_INSTALL_PREFIX}"
	RESULT_VARIABLE install_rc)
if(NOT install_rc EQUAL 0)
	message(FATAL_ERROR "qfw-slurm install-tree smoke install failed")
endif()

foreach(command_path
		bin/qfw-slurm-driver
		libexec/qfw-slurm/qfw-slurm-bb)
	if(NOT EXISTS "${QFW_SLURM_INSTALL_PREFIX}/${command_path}")
		message(FATAL_ERROR "missing installed command: ${command_path}")
	endif()
endforeach()

if(NOT EXISTS
	"${QFW_SLURM_INSTALL_PREFIX}/${QFW_SLURM_PLUGIN_DIR}/spank_quantum.so")
	message(FATAL_ERROR "missing installed SPANK module")
endif()

foreach(installed_file
		share/qfw-slurm/config/plugin.conf.example
		share/qfw-slurm/config/resources.lua.example
		share/qfw-slurm/config/burst_buffer.conf.example
		share/qfw-slurm/config/burst-buffer.lua.conf.example
		share/qfw-slurm/config/gateway.yaml.example
		share/qfw-slurm/config/plugstack.conf.example
		share/qfw-slurm/config/qfw-slurm-gateway.env.example
		share/qfw-slurm/slurm/job_submit.lua
		share/qfw-slurm/slurm/burst_buffer.lua
		${QFW_SLURM_SYSTEMD_DIR}/qfw-slurm-gateway.service
		${QFW_SLURM_INSTALL_LIBEXECDIR}/qfw-slurm/qfw_slurm_install.sh
		${QFW_SLURM_INSTALL_LIBEXECDIR}/qfw-slurm/qfw-slurm-bb)
	if(NOT EXISTS "${QFW_SLURM_INSTALL_PREFIX}/${installed_file}")
		message(FATAL_ERROR "missing installed file: ${installed_file}")
	endif()
endforeach()

set(man_root
	"${QFW_SLURM_INSTALL_PREFIX}/${QFW_SLURM_INSTALL_MANDIR}")
foreach(man_page
		man1/qfw-slurm-driver.1
		man1/qfw_slurm_install.sh.1
		man5/qfw-slurm-burst-buffer.conf.5
		man5/qfw-slurm-gateway.yaml.5
		man5/qfw-slurm-plugin.conf.5
		man7/qfw-slurm.7
		man8/qfw-slurm-bb.8
		man8/qfw-slurm-gateway-launch.8
		man8/qfw-slurm-gateway.8)
	if(NOT EXISTS "${man_root}/${man_page}")
		message(FATAL_ERROR "missing installed manual page: ${man_page}")
	endif()
endforeach()

find_program(QFW_SLURM_MAN man)
if(QFW_SLURM_MAN)
	execute_process(
		COMMAND "${CMAKE_COMMAND}" -E env
			"MANPATH=${man_root}"
			"${QFW_SLURM_MAN}" -w qfw-slurm-driver
		RESULT_VARIABLE man_lookup_rc
		OUTPUT_VARIABLE man_lookup_out
		ERROR_VARIABLE man_lookup_err)
	if(NOT man_lookup_rc EQUAL 0)
		message(FATAL_ERROR
			"installed qfw-slurm manual lookup failed\n"
			"stdout:\n${man_lookup_out}\n"
			"stderr:\n${man_lookup_err}")
	endif()
endif()
