cmake_minimum_required(VERSION 3.20)

project(qfw_slurm VERSION 0.1.0 LANGUAGES C)

include(CTest)
include(GNUInstallDirs)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

option(QFW_SLURM_BUILD_PLUGIN "Build the Slurm SPANK plugin" ON)
option(QFW_SLURM_BUILD_TESTS "Build native tests" ON)

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

if(QFW_SLURM_BUILD_TESTS)
	add_executable(test_qsgp_protocol tests/protocol/test_qsgp_protocol.c)
	target_link_libraries(test_qsgp_protocol PRIVATE qsgp)
	target_compile_options(test_qsgp_protocol PRIVATE
		-Wall -Wextra -Werror -Wpedantic)
	add_test(NAME qsgp_protocol COMMAND test_qsgp_protocol)
endif()
