cmake_minimum_required(VERSION 3.10)
project(HybridFTPSystem VERSION 1.0 LANGUAGES CXX)

# ============================================================================
# 1. CẤU HÌNH TRUẨN C++ VÀ COMPILER OPTIONS
# ============================================================================
# Bắt buộc sử dụng chuẩn C++11 (cần thiết cho std::thread và các tính năng OS native)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Bật tất cả cảnh báo (Warnings) để hỗ trợ debug code tốt hơn
add_compile_options(-Wall -Wextra)

# ============================================================================
# 2. TÌM KIẾM CÁC THƯ VIỆN HỆ THỐNG CẦN THIẾT
# ============================================================================
# Tìm thư viện Threads (pthread trên Linux/WSL) để xử lý đa luồng (Concurrency)
find_package(Threads REQUIRED)

# Tìm thư viện OpenSSL (cung cấp libcrypto để tính mã SHA-256 / MD5 cho lệnh HASH)
find_package(OpenSSL REQUIRED)

# ============================================================================
# 3. INCLUDE DIRECTORIES (Cấu hình đường dẫn chứa file Header .h)
# ============================================================================
include_directories(
    ${CMAKE_SOURCE_DIR}/common
    ${CMAKE_SOURCE_DIR}/server/tcp_control
    ${CMAKE_SOURCE_DIR}/server/udp_data
    ${CMAKE_SOURCE_DIR}/client/tcp_client
    ${CMAKE_SOURCE_DIR}/client/udp_client
    ${OPENSSL_INCLUDE_DIR}
)

# ============================================================================
# 4. BUILD FTP SERVER (Thành phần Server)
# ============================================================================
# Danh sách tất cả file .cpp cho phía Server
set(SERVER_SOURCES
    # --- PHẦN VIỆC CỦA BẠN (TCP Control Channel) ---
    server/tcp_control/tcp_server.cpp
    server/tcp_control/file_system.cpp
    
    # --- PHẦN VIỆC CỦA ĐỒNG ĐỘI (UDP Data Channel / RDT) ---
    # Khi đồng đội viết xong file nào, bạn chỉ cần bỏ dấu '#' ở đầu dòng đó:
    # server/udp_data/rdt_sender.cpp
    # server/udp_data/rdt_receiver.cpp
    # server/udp_data/active_passive.cpp
)

# Tạo file thực thi 'ftp_server'
add_executable(ftp_server ${SERVER_SOURCES})

# Liên kết (Link) các thư viện Threads và OpenSSL Crypto vào Server
target_link_libraries(ftp_server PRIVATE 
    Threads::Threads 
    OpenSSL::Crypto
)

# ============================================================================
# 5. BUILD FTP CLIENT (Thành phần Client)
# ============================================================================
# Danh sách tất cả file .cpp cho phía Client
set(CLIENT_SOURCES
    # --- PHẦN VIỆC CỦA BẠN (TCP Control Client) ---
    client/tcp_client/control_client.cpp
    # client/tcp_client/ui_interface.cpp
    
    # --- PHẦN VIỆC CỦA ĐỒNG ĐỘI (UDP Client / Progress Bar) ---
    # Khi đồng đội viết xong file nào, bạn chỉ cần bỏ dấu '#' ở đầu dòng đó:
    # client/udp_client/client_rdt.cpp
    # client/udp_client/progress_bar.cpp
)

# Tạo file thực thi 'ftp_client'
add_executable(ftp_client ${CLIENT_SOURCES})

# Liên kết (Link) các thư viện Threads và OpenSSL Crypto vào Client
target_link_libraries(ftp_client PRIVATE 
    Threads::Threads 
    OpenSSL::Crypto
)

# ============================================================================
# 6. HIỂN THỊ THÔNG BÁO HOÀN TẤT CẤU HÌNH
# ============================================================================
message(STATUS "----------------------------------------------------")
message(STATUS " Hybrid FTP System CMake configured successfully!")
message(STATUS " C++ Standard: C++${CMAKE_CXX_STANDARD}")
message(STATUS " OpenSSL Found: ${OPENSSL_FOUND}")
message(STATUS "----------------------------------------------------")