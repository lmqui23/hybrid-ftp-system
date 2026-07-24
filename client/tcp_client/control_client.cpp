#include <iostream>
#include <string>
#include <sstream>
#include <cstring>
#include <vector>
#include <algorithm>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#define DEFAULT_PORT 2121
#define DEFAULT_IP "127.0.0.1"
#define BUFFER_SIZE 4096

class FTPClient {
private:
    int tcp_fd;
    bool connected;

    // Receive server response from TCP control channel
    std::string receive_response() {
        char buffer[BUFFER_SIZE];
        memset(buffer, 0, BUFFER_SIZE);

        ssize_t bytes_read = recv(tcp_fd, buffer, BUFFER_SIZE - 1, 0);

        if (bytes_read <= 0) {
            connected = false;
            return "";
        }

        return std::string(buffer);
    }

    // Send raw command string over TCP control connection
    bool send_command(const std::string& cmd) {
        std::string full_cmd = cmd + "\r\n";
        ssize_t bytes_sent = send(tcp_fd, full_cmd.c_str(), full_cmd.length(), 0);
        return bytes_sent > 0;
    }

    // Helper to extract response status code (e.g., 150, 220, 226)
    int get_response_code(const std::string& response) {
        if (response.length() < 3) return -1;
        try {
            return std::stoi(response.substr(0, 3));
        } catch (...) {
            return -1;
        }
    }

    // Placeholder/Handler for UDP Data transfer commands (LIST, RETR, STOR)
    void handle_data_transfer(const std::string& cmd_line) {
        // 1. Send the command over TCP control connection
        if (!send_command(cmd_line)) {
            std::cout << "[Error] Failed to send command to server.\n";
            return;
        }

        // 2. Read initial response (Expected: 150 File status okay / Opening data connection)
        std::string response = receive_response();
        std::cout << response;

        int code = get_response_code(response);
        if (code == 150) {
            // TODO: Execute UDP RDT protocol logic here
            // e.g., udp_client_receive_file() or udp_client_send_file()
            
            // 3. Read final completion response from server (Expected: 226 Transfer Complete)
            std::string final_res = receive_response();
            std::cout << final_res;
        }
    }

public:
    FTPClient() : tcp_fd(-1), connected(false) {}

    ~FTPClient() {
        disconnect();
    }

    // Connect to FTP server control port
    bool connect_to_server(const std::string& ip, int port) {
        tcp_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (tcp_fd < 0) {
            std::cerr << "[Error] Failed to create socket!\n";
            return false;
        }

        sockaddr_in server_addr{};
        server_addr.sin_family = AF_INET;
        server_addr.sin_port = htons(port);

        if (inet_pton(AF_INET, ip.c_str(), &server_addr.sin_addr) <= 0) {
            std::cerr << "[Error] Invalid IP address format!\n";
            close(tcp_fd);
            return false;
        }

        if (connect(tcp_fd, (struct sockaddr*)&server_addr, sizeof(server_addr)) < 0) {
            std::cerr << "[Error] Connection to server failed.\n";
            close(tcp_fd);
            return false;
        }

        connected = true;

        // Print welcome message (220)
        std::cout << receive_response();
        return true;
    }

    // Disconnect safely from FTP server
    void disconnect() {
        if (connected) {
            send_command("QUIT");
            std::cout << receive_response();
            close(tcp_fd);
            connected = false;
        }
    }

    // Main Interactive CLI Loop
    void run_cli() {
        std::string user_input;

        std::cout << "====================================================\n";
        std::cout << "   HYBRID FTP CLIENT CLI - READY FOR COMMANDS       \n";
        std::cout << "====================================================\n";
        std::cout << " [Auth/Session]: USER, PASS, QUIT\n";
        std::cout << " [Data Mode]   : PASV, PORT, TYPE\n";
        std::cout << " [Directory]   : PWD, CWD, CDUP, MKD, RMD\n";
        std::cout << " [File/Data]   : LIST, NLST, RETR, STOR, APPE, STOU, DELE, RNFR, RNTO, ABOR\n";
        std::cout << " [Info/Meta]   : STAT, SIZE, MDTM, HASH\n";
        std::cout << "====================================================\n\n";
        while (connected) {
            std::cout << "ftp> ";
            if (!std::getline(std::cin, user_input) || user_input.empty()) {
                continue;
            }

            // Extract command name in uppercase for comparison
            std::stringstream ss(user_input);
            std::string cmd;
            ss >> cmd;
            std::transform(cmd.begin(), cmd.end(), cmd.begin(), ::toupper);

            if (cmd == "EXIT" || cmd == "QUIT") {
                disconnect();
                break;
            }

            // Data transfer commands require two-step TCP response handling + UDP logic
            if (cmd == "LIST" || cmd == "NLST" || cmd == "RETR" || cmd == "STOR" || cmd == "APPE") {
                handle_data_transfer(user_input);
            } 
            else {
                // Standard control-only commands
                if (send_command(user_input)) {
                    std::string response = receive_response();
                    if (response.empty()) {
                        std::cout << "[Client] Server closed the connection.\n";
                        break;
                    }
                    std::cout << response;
                } else {
                    std::cout << "[Error] Failed to send command!\n";
                }
            }
        }
    }
};

int main(int argc, char* argv[]) {
    std::string ip = DEFAULT_IP;
    int port = DEFAULT_PORT;

    if (argc >= 2) ip = argv[1];
    if (argc >= 3) port = std::atoi(argv[2]);

    if (port <= 0 || port > 65535) {
        std::cerr << "[Fatal] Invalid port number: " << port << std::endl;
        return 1;
    }

    FTPClient client;
    std::cout << "[Client] Connecting to " << ip << ":" << port << "...\n";

    if (client.connect_to_server(ip, port)) {
        client.run_cli();
    }

    return 0;
}