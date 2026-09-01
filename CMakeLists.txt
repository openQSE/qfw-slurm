cmake_minimum_required(VERSION 3.20)

project(qfw_slurm VERSION 0.1.0 LANGUAGES C)

include(CTest)
include(GNUInstallDirs)

add_subdirectory(man)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

option(QFW_SLURM_BUILD_PLUGIN "Build the Slurm SPANK plugin" ON)
option(QFW_SLURM_BUILD_DRIVER "Build the standalone diagnostic driver" ON)
option(QFW_SLURM_BUILD_TESTS "Build native tests" ON)
set(QFW_SLURM_PLUGIN_DIR "${CMAKE_INSTALL_LIBDIR}/slurm" CACHE PATH
	"Slurm plugin installation directory")
set(QFW_SLURM_SYSTEMD_DIR "${CMAKE_INSTALL_LIBDIR}/systemd/system" CACHE PATH
	"systemd unit installation directory")

find_package(PkgConfig REQUIRED)
pkg_check_modules(MUNGE REQUIRED IMPORTED_TARGET munge)

add_library(qsgp STATIC
	src/protocol/qsgp_encode.c
	src/protocol/qsgp_decode.c
	src/protocol/qsgp_io.c
	src/protocol/qsgp_munge.c
)
target_include_directories(qsgp
	PUBLIC
		$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
		$<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
	PRIVATE
		${CMAKE_CURRENT_SOURCE_DIR}/src/protocol
)
target_link_libraries(qsgp PUBLIC PkgConfig::MUNGE)
target_compile_options(qsgp PRIVATE -Wall -Wextra -Werror -Wpedantic)
set_target_properties(qsgp PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(qfw_slurm_client STATIC
	src/native/qsgp_client.c
	src/native/plugin_config.c
)
target_include_directories(qfw_slurm_client
	PUBLIC
		${CMAKE_CURRENT_SOURCE_DIR}/src/native
)
target_link_libraries(qfw_slurm_client PUBLIC qsgp)
target_compile_options(qfw_slurm_client PRIVATE
	-Wall -Wextra -Werror -Wpedantic)
set_target_properties(qfw_slurm_client PROPERTIES
	POSITION_INDEPENDENT_CODE ON)

add_library(qfw_slurm_operations STATIC
	src/native/options.c
	src/native/reservation_transaction.c
	src/native/environment.c
	src/native/operations.c
)
target_include_directories(qfw_slurm_operations
	PUBLIC
		${CMAKE_CURRENT_SOURCE_DIR}/src/native
)
target_link_libraries(qfw_slurm_operations PUBLIC qfw_slurm_client)
target_compile_options(qfw_slurm_operations PRIVATE
	-Wall -Wextra -Werror -Wpedantic)
set_target_properties(qfw_slurm_operations PROPERTIES
	POSITION_INDEPENDENT_CODE ON)

if(QFW_SLURM_BUILD_DRIVER)
	add_executable(qfw-slurm-driver src/driver/qfw_slurm_driver.c)
	target_link_libraries(qfw-slurm-driver PRIVATE qfw_slurm_operations)
	target_compile_options(qfw-slurm-driver PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	install(TARGETS qfw-slurm-driver
		RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()

if(QFW_SLURM_BUILD_PLUGIN)
	find_path(SLURM_INCLUDE_DIR slurm/spank.h REQUIRED)
	find_library(SLURM_LIBRARY slurm REQUIRED)

	add_library(spank_quantum MODULE src/plugin/spank_quantum.c)
	target_include_directories(spank_quantum PRIVATE ${SLURM_INCLUDE_DIR})
	target_link_libraries(spank_quantum PRIVATE
		qfw_slurm_operations ${SLURM_LIBRARY})
	target_compile_options(spank_quantum PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	set_target_properties(spank_quantum PROPERTIES PREFIX "")
	install(TARGETS spank_quantum
		LIBRARY DESTINATION ${QFW_SLURM_PLUGIN_DIR})

	add_executable(qfw-slurm-epilog src/epilog/qfw_slurm_epilog.c)
	target_link_libraries(qfw-slurm-epilog PRIVATE qfw_slurm_operations)
	target_compile_options(qfw-slurm-epilog PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	install(TARGETS qfw-slurm-epilog
		RUNTIME DESTINATION ${CMAKE_INSTALL_SBINDIR})
endif()

install(FILES
	config/plugin.conf.example
	config/gateway.yaml.example
	config/plugstack.conf.example
	config/qfw-slurm-gateway.env.example
	DESTINATION ${CMAKE_INSTALL_DATADIR}/qfw-slurm/config)
install(FILES systemd/qfw-slurm-gateway.service
	DESTINATION ${QFW_SLURM_SYSTEMD_DIR})

if(QFW_SLURM_BUILD_TESTS)
	add_executable(test_qsgp_protocol tests/protocol/test_qsgp_protocol.c)
	target_link_libraries(test_qsgp_protocol PRIVATE qsgp)
	target_compile_options(test_qsgp_protocol PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	add_test(NAME qsgp_protocol COMMAND test_qsgp_protocol)

	add_executable(qsgp_interop tests/protocol/qsgp_interop.c)
	target_link_libraries(qsgp_interop PRIVATE qsgp)
	target_compile_options(qsgp_interop PRIVATE
		-Wall -Wextra -Werror -Wpedantic)

	add_executable(test_qfw_native tests/unit/test_qfw_native.c)
	target_link_libraries(test_qfw_native PRIVATE qfw_slurm_operations)
	target_compile_options(test_qfw_native PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	add_test(NAME qfw_native COMMAND test_qfw_native)

	add_executable(test_gateway_client tests/unit/test_gateway_client.c)
	target_link_libraries(test_gateway_client PRIVATE qfw_slurm_client)
	target_compile_options(test_gateway_client PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	add_test(NAME gateway_client COMMAND test_gateway_client)
	if(QFW_SLURM_BUILD_DRIVER)
		add_test(NAME driver_cli
			COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/tests/unit/test_driver_cli.sh
				$<TARGET_FILE:qfw-slurm-driver>)
	endif()

	find_package(Python3 COMPONENTS Interpreter)
	if(Python3_Interpreter_FOUND)
		execute_process(
			COMMAND ${Python3_EXECUTABLE} -c "import yaml"
			RESULT_VARIABLE QFW_SLURM_SYSTEM_TEST_DEPS
			ERROR_QUIET)
		execute_process(
			COMMAND ${Python3_EXECUTABLE} -c "import pytest, yaml"
			RESULT_VARIABLE QFW_SLURM_PYTHON_TEST_DEPS
			ERROR_QUIET)
		if(QFW_SLURM_PYTHON_TEST_DEPS EQUAL 0)
			add_test(NAME qfw_gateway
				COMMAND ${CMAKE_COMMAND} -E env
					PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/gateway
					QSGP_INTEROP=$<TARGET_FILE:qsgp_interop>
					${Python3_EXECUTABLE} -m pytest -q
					${CMAKE_CURRENT_SOURCE_DIR}/tests/gateway)
		else()
			message(STATUS
				"Skipping gateway tests: pytest and PyYAML are required")
		endif()
		if(QFW_SLURM_BUILD_DRIVER AND
		   QFW_SLURM_SYSTEM_TEST_DEPS EQUAL 0)
			add_test(NAME driver_gateway_system
				COMMAND ${CMAKE_COMMAND} -E env
					PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}/gateway:${CMAKE_CURRENT_SOURCE_DIR}
					QFW_TEST_PYTHON=${Python3_EXECUTABLE}
					${CMAKE_CURRENT_SOURCE_DIR}/tests/system/test_driver_gateway.sh
					$<TARGET_FILE:qfw-slurm-driver>)
			set_tests_properties(driver_gateway_system PROPERTIES
				SKIP_RETURN_CODE 77 TIMEOUT 60)
		endif()
	endif()
endif()
