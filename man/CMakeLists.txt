set(QFW_SLURM_MAN1_PAGES
	man1/qfw-slurm-driver.1
	man1/qfw_slurm_install.sh.1)

set(QFW_SLURM_MAN5_PAGES
	man5/qfw-slurm-burst-buffer.conf.5
	man5/qfw-slurm-gateway.yaml.5
	man5/qfw-slurm-plugin.conf.5)

set(QFW_SLURM_MAN7_PAGES
	man7/qfw-slurm.7)

set(QFW_SLURM_MAN8_PAGES
	man8/qfw-slurm-bb.8
	man8/qfw-slurm-gateway-launch.8
	man8/qfw-slurm-gateway.8)

foreach(qfw_slurm_man_section RANGE 1 9)
	set(qfw_slurm_man_list
		"QFW_SLURM_MAN${qfw_slurm_man_section}_PAGES")
	foreach(qfw_slurm_man_page IN LISTS ${qfw_slurm_man_list})
		install(FILES "${qfw_slurm_man_page}"
			DESTINATION
				"${CMAKE_INSTALL_MANDIR}/man${qfw_slurm_man_section}"
			COMPONENT documentation)
	endforeach()
endforeach()
