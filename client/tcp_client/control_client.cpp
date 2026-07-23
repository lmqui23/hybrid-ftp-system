#include <iostream>
#include <string>
#include <sstream>
#include <cstring>
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

    // Send an FTP command to the server
    bool send_command(const std::string& cmd) {

        std::string full_cmd = cmd + "\r\n";

        ssize_t bytes_sent =
            send(tcp_fd,
                 full_cmd.c_str(),
                 full_cmd.length(),
                 0);

        return bytes_sent > 0;
    }

public:

    FTPClient() : tcp_fd(-1), connected(false) {}

    ~FTPClient() {
        disconnect();
    }

    // Connect to FTP server
    bool connect_to_server(const std::string& ip, int port) {

        tcp_fd = socket(AF_INET, SOCK_STREAM, 0);

        if (tcp_fd < 0) {
            std::cerr << "[Error] Cannot create socket!\n";
            return false;
        }

        sockaddr_in server_addr;

        memset(&server_addr, 0, sizeof(server_addr));

        server_addr.sin_family = AF_INET;
        server_addr.sin_port = htons(port);

        inet_pton(AF_INET,
                  ip.c_str(),
                  &server_addr.sin_addr);

        if (connect(tcp_fd,
                    (struct sockaddr*)&server_addr,
                    sizeof(server_addr)) < 0) {

            std::cerr << "[Error] Cannot connect to server.\n";

            close(tcp_fd);

            return false;
        }

        connected = true;

        // Print welcome message (220)
        std::cout << receive_response();

        return true;
    }

    // Disconnect from server
    void disconnect() {

        if (connected) {

            send_command("QUIT");

            std::cout << receive_response();

            close(tcp_fd);

            connected = false;
        }
    }

    // Run interactive FTP command line
    void run_cli() {

        std::string user_input;

        std::cout << "\n====================================================\n";
        std::cout << "   HYBRID FTP CLIENT CLI - READY FOR COMMANDS       \n";
        std::cout << "====================================================\n";
        std::cout << "Supported commands: USER, PASS, PWD, CWD, CDUP, MKD, RMD, HASH, LIST, RETR, STOR, QUIT\n\n";

        while (connected) {

            std::cout << "ftp> ";

            if (!std::getline(std::cin, user_input) ||
                user_input.empty()) {

                continue;
            }

            if (user_input == "exit" ||
                user_input == "QUIT") {

                disconnect();

                break;
            }

            // Send command and print server response
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
};

int main(int argc, char* argv[]) {

    std::string ip = DEFAULT_IP;

    int port = DEFAULT_PORT;

    if (argc >= 2)
        ip = argv[1];

    if (argc >= 3)
        port = std::atoi(argv[2]);

    FTPClient client;

    std::cout << "[Client] Connecting to "
              << ip
              << ":"
              << port
              << "...\n";

    if (client.connect_to_server(ip, port)) {

        client.run_cli();

    }

    return 0;
}