#include <iostream>
#include <string>
#include <sstream>
#include <vector>
#include <algorithm>
#include <cstring>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <cstdlib>
#include <chrono>
#include <iomanip>

#include "ftp_shared.h"
#include "file_system.h"

#define CONTROL_PORT 2121
#define BUFFER_SIZE 1024

// ============================================================================
// HELPER FUNCTIONS & LOGGING
// ============================================================================

// Get current timestamp formatted as [YYYY-MM-DD HH:MM:SS]
std::string get_current_timestamp() {
    auto now = std::chrono::system_clock::now();
    auto in_time_t = std::chrono::system_clock::to_time_t(now);
    std::stringstream ss;
    ss << std::put_time(std::localtime(&in_time_t), "%Y-%m-%d %H:%M:%S");
    return ss.str();
}

// Unified logger function for server activity
void log_server(const std::string& level, int client_fd, const std::string& message) {
    std::cout << "[" << get_current_timestamp() << "] [" << level << "] [FD " << client_fd << "] " 
              << message << std::endl;
}

// Send standard FTP reply to client
void send_reply(int client_fd, int code, const std::string& message) {
    std::string response = std::to_string(code) + " " + message + "\r\n";
    ssize_t bytes_sent = send(client_fd, response.c_str(), response.length(), 0);
    if (bytes_sent <= 0) {
        log_server("ERROR", client_fd, "Failed to send response or client disconnected unexpectedly.");
    }
}

// Convert absolute OS path to relative FTP virtual path (e.g., /docs)
std::string get_ftp_path(const std::string& full_path, const std::string& root_dir) {
    if (full_path == root_dir) return "/";
    if (full_path.rfind(root_dir, 0) == 0) { // full_path starts with root_dir
        std::string rel = full_path.substr(root_dir.length());
        return rel.empty() ? "/" : rel;
    }
    return "/";
}

// Safe path resolver: Prevents Path Traversal attacks (../..)
// Returns true if the path is valid and strictly contained within root_dir
bool resolve_safe_path(const std::string& base_dir, const std::string& user_input, const std::string& root_dir, std::string& out_abs_path) {
    std::string target;
    if (user_input.empty()) {
        target = base_dir;
    } else if (user_input[0] == '/') {
        target = root_dir + user_input;
    } else {
        target = base_dir + "/" + user_input;
    }

    char resolved[1024];
    // Case 1: Existing file or directory
    if (realpath(target.c_str(), resolved) != nullptr) {
        out_abs_path = std::string(resolved);
    } else {
        // Case 2: Non-existent target (e.g., STOR, MKD), check parent directory
        size_t last_slash = target.find_last_of('/');
        if (last_slash == std::string::npos) return false;

        std::string parent_dir = target.substr(0, last_slash);
        std::string filename = target.substr(last_slash + 1);

        char resolved_parent[1024];
        if (realpath(parent_dir.c_str(), resolved_parent) == nullptr) {
            return false; // Parent directory does not exist
        }

        out_abs_path = std::string(resolved_parent) + "/" + filename;
    }

    // SECURITY CHECK: Verify resolved path is within root_dir
    if (out_abs_path.length() < root_dir.length() ||
        out_abs_path.compare(0, root_dir.length(), root_dir) != 0) {
        return false; // Path Traversal attempt detected!
    }

    // Prevent prefix trickery (e.g., /storage/server_files_fake)
    if (out_abs_path.length() > root_dir.length() && out_abs_path[root_dir.length()] != '/') {
        return false;
    }

    return true;
}

// Retrieve local bound IP address for PASV mode response
std::string get_local_ip(int client_fd) {
    sockaddr_in local_addr{};
    socklen_t addr_len = sizeof(local_addr);
    if (getsockname(client_fd, (struct sockaddr*)&local_addr, &addr_len) == 0) {
        char ip_str[INET_ADDRSTRLEN];
        inet_ntop(AF_INET, &(local_addr.sin_addr), ip_str, INET_ADDRSTRLEN);
        std::string ip(ip_str);
        if (ip != "0.0.0.0") return ip;
    }
    return "127.0.0.1"; // Fallback to loopback
}

// ============================================================================
// FTP CLIENT SESSION HANDLER (CONTROL CONNECTION)
// ============================================================================

void handle_client_session(int client_fd, const std::string& client_ip, int client_port) {
    FTPSession session(client_fd, client_ip, client_port);

    log_server("INFO", client_fd, "Session initialized for " + client_ip + ":" + std::to_string(client_port));
    send_reply(client_fd, 220, "Welcome to RDT-FTP Server (Hybrid TCP/UDP)");

    char buffer[BUFFER_SIZE];
    while (true) {
        memset(buffer, 0, BUFFER_SIZE);
        ssize_t bytes_read = recv(client_fd, buffer, BUFFER_SIZE - 1, 0);

        if (bytes_read <= 0) {
            log_server("INFO", client_fd, "Client disconnected or connection lost.");
            break;
        }

        std::string request(buffer);
        // Strip trailing CR/LF characters
        request.erase(request.find_last_not_of("\r\n") + 1);

        if (request.empty()) continue;

        std::stringstream ss(request);
        std::string cmd, arg;
        ss >> cmd;
        std::getline(ss >> std::ws, arg);

        // Convert command to uppercase
        std::transform(cmd.begin(), cmd.end(), cmd.begin(), ::toupper);

        // LOG RECEIVED COMMAND
        log_server("INFO", client_fd, "Command received: " + cmd + (arg.empty() ? "" : " " + arg));

        // --------------------------------------------------------------------
        // 1. AUTHENTICATION & SESSION CONTROL
        // --------------------------------------------------------------------
        if (cmd == "USER") {
            session.username = arg;
            send_reply(client_fd, 331, "User name okay, need password.");
        } 
        else if (cmd == "PASS") {
            if (session.username.empty()) {
                send_reply(client_fd, 503, "Bad sequence of commands. Send USER first.");
            } else if (FileSystem::verify_user_credentials(session.username, arg)) {
                session.is_authenticated = true;
                log_server("INFO", client_fd, "User '" + session.username + "' authenticated successfully.");
                send_reply(client_fd, 230, "User logged in, proceed.");
            } else {
                log_server("WARN", client_fd, "Authentication failed for user '" + session.username + "'");
                send_reply(client_fd, 530, "Authentication failed.");
            }
        }
        else if (!session.is_authenticated && cmd != "QUIT") {
            send_reply(client_fd, 530, "Please login with USER and PASS.");
            continue;
        }

        // --------------------------------------------------------------------
        // 2. DATA TRANSFER MODE SWITCH (PASV / PORT / TYPE)
        // --------------------------------------------------------------------
        else if (cmd == "PASV") {
            int port = udp_prepare_passive_listener(&session);
            std::string local_ip = get_local_ip(client_fd);

            // Replace '.' with ',' for FTP PASV response format
            std::replace(local_ip.begin(), local_ip.end(), '.', ',');

            std::string pasv_msg = "Entering Passive Mode (" + local_ip + "," + 
                                  std::to_string(port / 256) + "," + 
                                  std::to_string(port % 256) + ")";
            send_reply(client_fd, 227, pasv_msg);
        }
        else if (cmd == "PORT") {
            // Parse PORT format: h1,h2,h3,h4,p1,p2
            std::replace(arg.begin(), arg.end(), ',', ' ');
            std::stringstream port_ss(arg);
            int h1, h2, h3, h4, p1, p2;
            if (port_ss >> h1 >> h2 >> h3 >> h4 >> p1 >> p2) {
                if (h1 < 0 || h1 > 255 || h2 < 0 || h2 > 255 || 
                    h3 < 0 || h3 > 255 || h4 < 0 || h4 > 255) {
                    send_reply(client_fd, 501, "Invalid IP or Port range.");
                    continue;
                }

                int port = p1 * 256 + p2;
                if (port < 1024 || port > 65535) {
                    send_reply(client_fd, 501, "Port number must be >= 1024.");
                    continue;
                }

                std::string ip = std::to_string(h1) + "." + std::to_string(h2) + "." + 
                                 std::to_string(h3) + "." + std::to_string(h4);
                udp_set_active_target(&session, ip, port);
                send_reply(client_fd, 200, "PORT command successful.");
            } else {
                send_reply(client_fd, 501, "Syntax error in IP/PORT.");
            }
        }
        else if (cmd == "TYPE") {
            if (arg == "A" || arg == "a") {
                session.type = TYPE_ASCII;
                send_reply(client_fd, 200, "Switching to ASCII mode.");
            } else if (arg == "I" || arg == "i") {
                session.type = TYPE_BINARY;
                send_reply(client_fd, 200, "Switching to Binary mode.");
            } else {
                send_reply(client_fd, 504, "Command not implemented for that parameter.");
            }
        }

        // --------------------------------------------------------------------
        // 3. DIRECTORY & PATH OPERATIONS
        // --------------------------------------------------------------------
        else if (cmd == "PWD") {
            std::string vpath = get_ftp_path(session.current_dir, session.root_dir);
            send_reply(client_fd, 257, "\"" + vpath + "\" is the current directory.");
        }
        else if (cmd == "CWD") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                !FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "Failed to change directory. Invalid path or not a directory.");
            } else {
                session.current_dir = target_path;
                send_reply(client_fd, 250, "Directory successfully changed to " + get_ftp_path(target_path, session.root_dir));
            }
        }
        else if (cmd == "CDUP") {
            std::string target_path;
            // Move up to parent directory '..'
            if (!resolve_safe_path(session.current_dir, "..", session.root_dir, target_path) ||
                !FileSystem::is_directory(target_path)) {
                session.current_dir = session.root_dir;
                send_reply(client_fd, 250, "Directory successfully changed to /");
            } else {
                session.current_dir = target_path;
                send_reply(client_fd, 250, "Directory successfully changed to " + get_ftp_path(target_path, session.root_dir));
            }
        }
        else if (cmd == "MKD") {
            if (arg.empty()) {
                send_reply(client_fd, 501, "Syntax error in parameters.");
                continue;
            }
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                send_reply(client_fd, 550, "Create directory operation failed. Path traversal denied.");
            } else if (FileSystem::exists(target_path)) {
                send_reply(client_fd, 550, "Create directory failed. Path already exists.");
            } else if (FileSystem::create_directory(target_path)) {
                send_reply(client_fd, 257, "\"" + get_ftp_path(target_path, session.root_dir) + "\" created.");
            } else {
                send_reply(client_fd, 550, "Create directory failed.");
            }
        }
        else if (cmd == "RMD") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                !FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "Remove directory failed. Directory does not exist or access denied.");
            } else if (target_path == session.root_dir) {
                send_reply(client_fd, 550, "Cannot remove root directory.");
            } else if (FileSystem::remove_directory(target_path)) {
                send_reply(client_fd, 250, "Directory removed.");
            } else {
                send_reply(client_fd, 550, "Remove directory failed. Directory might not be empty.");
            }
        }

        // --------------------------------------------------------------------
        // 4. LISTING, STATS & METADATA
        // --------------------------------------------------------------------
        else if (cmd == "LIST" || cmd == "NLST") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                !FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "Directory not found or access denied.");
                continue;
            }

            send_reply(client_fd, 150, "File status okay; about to open data connection.");
            
            std::string listing = (cmd == "LIST") ? 
                FileSystem::get_directory_listing(target_path) : 
                FileSystem::get_simple_listing(target_path);

            TransferResult res = udp_send_buffer(&session, listing.c_str(), listing.length());
            if (res.is_success) {
                send_reply(client_fd, 226, "Closing data connection. Transfer successful.");
            } else {
                send_reply(client_fd, 426, "Data connection closed; transfer aborted.");
            }
        }
        else if (cmd == "STAT") {
            if (arg.empty()) {
                std::string status = "Server status: Connected\r\nMode: " + 
                                     std::string(session.mode == MODE_PASSIVE ? "PASV" : "ACTIVE") + 
                                     "\r\nUser: " + session.username;
                send_reply(client_fd, 211, status);
            } else {
                std::string target_path;
                if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                    send_reply(client_fd, 550, "Directory not found.");
                } else {
                    std::string listing = FileSystem::get_directory_listing(target_path);
                    send_reply(client_fd, 213, "Status follows:\r\n" + listing + "End of status.");
                }
            }
        }
        else if (cmd == "SIZE") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "Could not get file size.");
            } else {
                long long size = FileSystem::get_file_size(target_path);
                if (size >= 0) send_reply(client_fd, 213, std::to_string(size));
                else send_reply(client_fd, 550, "Could not get file size.");
            }
        }
        else if (cmd == "MDTM") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                send_reply(client_fd, 550, "Could not get file modification time.");
            } else {
                std::string mtime = FileSystem::get_file_mtime(target_path);
                if (!mtime.empty()) send_reply(client_fd, 213, mtime);
                else send_reply(client_fd, 550, "Could not get modification time.");
            }
        }
        else if (cmd == "HASH") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "File not found or is a directory.");
            } else {
                std::string hash = FileSystem::calculate_sha256(target_path);
                if (!hash.empty()) send_reply(client_fd, 200, "SHA-256 " + hash);
                else send_reply(client_fd, 550, "Failed to calculate hash.");
            }
        }

        // --------------------------------------------------------------------
        // 5. FILE TRANSFER OPERATIONS (RETR, STOR, APPE, STOU, DELE, RENAME)
        // --------------------------------------------------------------------
        else if (cmd == "RETR") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                send_reply(client_fd, 550, "Access denied. Path traversal blocked.");
            } else if (!FileSystem::exists(target_path) || FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "File not found or is a directory.");
            } else {
                send_reply(client_fd, 150, "Opening data connection for file download.");
                TransferResult res = udp_send_file(&session, target_path);
                if (res.is_success) {
                    send_reply(client_fd, 226, "Transfer complete.");
                } else {
                    send_reply(client_fd, 426, "Transfer aborted: " + res.error_msg);
                }
            }
        }
        else if (cmd == "STOR" || cmd == "APPE" || cmd == "STOU") {
            std::string target_path;
            
            if (cmd == "STOU") {
                std::string unique_name = FileSystem::generate_unique_filename(session.current_dir, session.control_fd);
                target_path = session.current_dir + "/" + unique_name;
                send_reply(client_fd, 150, "FILE: " + unique_name);
            } else {
                if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                    send_reply(client_fd, 550, "Access denied. Invalid target path.");
                    continue;
                }
                send_reply(client_fd, 150, "Opening data connection for file upload.");
            }

            bool is_append = (cmd == "APPE");
            TransferResult res = udp_receive_file(&session, target_path, is_append);
            if (res.is_success) {
                send_reply(client_fd, 226, "Transfer complete.");
            } else {
                send_reply(client_fd, 426, "Transfer aborted: " + res.error_msg);
            }
        }
        else if (cmd == "DELE") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                FileSystem::is_directory(target_path)) {
                send_reply(client_fd, 550, "File not found or is a directory.");
            } else if (FileSystem::remove_file(target_path)) {
                send_reply(client_fd, 250, "File deleted successfully.");
            } else {
                send_reply(client_fd, 550, "Delete file failed.");
            }
        }
        else if (cmd == "RNFR") {
            std::string target_path;
            if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path) ||
                !FileSystem::exists(target_path)) {
                send_reply(client_fd, 550, "File or directory does not exist.");
            } else {
                session.rename_from_path = target_path;
                send_reply(client_fd, 350, "Requested file action pending further information.");
            }
        }
        else if (cmd == "RNTO") {
            if (session.rename_from_path.empty()) {
                send_reply(client_fd, 503, "Bad sequence of commands. Send RNFR first.");
            } else {
                std::string target_path;
                if (!resolve_safe_path(session.current_dir, arg, session.root_dir, target_path)) {
                    send_reply(client_fd, 550, "Invalid target path.");
                } else if (FileSystem::rename_path(session.rename_from_path, target_path)) {
                    send_reply(client_fd, 250, "File renamed successfully.");
                } else {
                    send_reply(client_fd, 550, "Rename failed.");
                }
                session.rename_from_path = "";
            }
        }
        else if (cmd == "NOOP") {
            send_reply(client_fd, 200, "NOOP ok.");
        }
        else if (cmd == "MODE") {
            if (arg == "S" || arg == "s") send_reply(client_fd, 200, "Mode set to Stream.");
            else send_reply(client_fd, 504, "Bad MODE parameter.");
        }
        else if (cmd == "ABOR") {
            udp_abort_transfer(&session);
            send_reply(client_fd, 226, "Abort command successful.");
        }
        else if (cmd == "QUIT") {
            log_server("INFO", client_fd, "Client requested disconnection (QUIT).");
            send_reply(client_fd, 221, "Goodbye.");
            break;
        }
        else {
            log_server("WARN", client_fd, "Unrecognized or unsupported command: " + cmd);
            send_reply(client_fd, 502, "Command not implemented.");
        }
    }

    close(client_fd);
    log_server("INFO", client_fd, "Session closed.");
}

// ============================================================================
// MAIN SERVER ENTRY POINT
// ============================================================================

int main() {
    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (server_fd < 0) {
        std::cerr << "[" << get_current_timestamp() << "] [FATAL] Failed to create control socket." << std::endl;
        return 1;
    }

    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(CONTROL_PORT);

    if (bind(server_fd, (struct sockaddr*)&address, sizeof(address)) < 0) {
        std::cerr << "[" << get_current_timestamp() << "] [FATAL] Bind failed on port " << CONTROL_PORT << std::endl;
        return 1;
    }

    if (listen(server_fd, 5) < 0) {
        std::cerr << "[" << get_current_timestamp() << "] [FATAL] Listen failed." << std::endl;
        return 1;
    }

    std::cout << "[" << get_current_timestamp() << "] [INFO] [SERVER] TCP Control Engine listening on port " 
              << CONTROL_PORT << "..." << std::endl;

    while (true) {
        sockaddr_in client_addr{};
        socklen_t addr_len = sizeof(client_addr);
        int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &addr_len);

        if (client_fd >= 0) {
            char client_ip[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &(client_addr.sin_addr), client_ip, INET_ADDRSTRLEN);
            int client_port = ntohs(client_addr.sin_port);

            handle_client_session(client_fd, std::string(client_ip), client_port);
        }
    }

    close(server_fd);
    return 0;
}