#ifndef FTP_SHARED_H
#define FTP_SHARED_H

#include <string>
#include <cstddef>
#include <cstdlib> // Cho hàm realpath()

// Data transfer mode
enum DataMode {
    MODE_NONE,
    MODE_ACTIVE,   // Client opens port, server connects (PORT)
    MODE_PASSIVE   // Server opens port, client connects (PASV)
};

// Data transfer type
enum TransferType {
    TYPE_ASCII,    // Text data
    TYPE_BINARY    // Binary data
};

// Store client FTP session information
struct FTPSession {
    int control_fd;        // Socket FD cho TCP Control (Dùng khớp với tcp_server.cpp)
    std::string client_ip; // Client IP
    int client_port;
    
    std::string username;
    bool is_authenticated;
    
    std::string root_dir; 
    std::string current_dir; 

    DataMode mode;
    std::string data_ip;
    int data_port;
    TransferType type;
    std::string rename_from_path;

    FTPSession(int fd, std::string ip = "127.0.0.1", int port = 0) 
        : control_fd(fd), client_ip(ip), client_port(port),
          username(""), is_authenticated(false),
          mode(MODE_NONE), data_ip(""), data_port(0), type(TYPE_BINARY), rename_from_path("") {
        
        char abs_path[1024];
        if (realpath("./storage/server_files", abs_path) != nullptr) {
            root_dir = std::string(abs_path);
        } else {
            root_dir = "./storage/server_files";
        }
        current_dir = root_dir;
    }
};

// Result returned from UDP engine to TCP engine
struct TransferResult {
    bool is_success;
    size_t bytes_transferred;
    int error_code;             // 0: OK, 1: Timeout, 2: File Error
    std::string error_msg;

    TransferResult(bool success = false, size_t bytes = 0, int err = 0, std::string msg = "")
        : is_success(success), bytes_transferred(bytes), error_code(err), error_msg(msg) {}
};

// ============================================================================
// Mock functions for UDP module integration
// Real implementation will be provided by UDP engine (rdt.cpp)
// ============================================================================

inline int udp_prepare_passive_listener(FTPSession* session) {
    session->mode = MODE_PASSIVE;
    session->data_port = 50005; 
    return session->data_port;
}

inline void udp_set_active_target(FTPSession* session, const std::string& ip, int port) {
    session->mode = MODE_ACTIVE;
    session->data_ip = ip;
    session->data_port = port;
}

inline TransferResult udp_send_file(FTPSession* session, const std::string& filepath) {
    (void)session; (void)filepath;
    return TransferResult(true, 1024, 0, "Success");
}

inline TransferResult udp_receive_file(FTPSession* session, const std::string& filepath, bool is_append) {
    (void)session; (void)filepath; (void)is_append;
    return TransferResult(true, 1024, 0, "Success");
}

inline TransferResult udp_send_buffer(FTPSession* session, const char* buffer, size_t len) {
    (void)session; (void)buffer; (void)len; // avoid warning unused-parameter
    return TransferResult(true, len, 0, "Success");
}

// Interface hủy truyền tải UDP
inline void udp_abort_transfer(FTPSession* session) {
    session->mode = MODE_NONE;
    
    // ĐỐI VỚI ĐỒNG ĐỘI (UDP DEV):
    // Người làm UDP sẽ thêm Atomic Flag: std::atomic<bool> is_aborted{false};
    // Hàm này sẽ set: session->is_aborted = true; để vỡ vòng lặp gửi/nhận UDP ngay lập tức.
}

#endif // FTP_SHARED_H