#include <iostream>
#include <string>
#include <sstream>
#include <thread>
#include <vector>
#include <cstring>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>

#include "../../common/ftp_shared.h"

#define TCP_PORT 2121
#define BUFFER_SIZE 1024

// Send FTP response code to client through TCP socket
void send_reply(int socket_fd, int code, const std::string& message) {
    std::string response = std::to_string(code) + " " + message + "\r\n";
    send(socket_fd, response.c_str(), response.length(), 0);
    std::cout << "[TCP TX -> " << socket_fd << "]: " << response;
}

// Handle each client in a separate thread (session isolation)
void handle_client(int client_fd) {
    FTPSession session(client_fd);
    
    // Send initial welcome message
    send_reply(client_fd, 220, "Hybrid FTP Service Ready.");

    char buffer[BUFFER_SIZE];
    while (true) {
        memset(buffer, 0, BUFFER_SIZE);
        int bytes_read = recv(client_fd, buffer, BUFFER_SIZE - 1, 0);

        if (bytes_read <= 0) {
            std::cout << "[TCP Control]: Client (FD: " << client_fd << ") disconnected.\n";
            break;
        }

        std::string raw_input(buffer);

        // Remove CRLF characters
        if (!raw_input.empty() && raw_input.back() == '\n') raw_input.pop_back();
        if (!raw_input.empty() && raw_input.back() == '\r') raw_input.pop_back();

        std::cout << "[TCP RX <- " << client_fd << "]: " << raw_input << std::endl;

        // Parse FTP command
        std::stringstream ss(raw_input);
        std::string cmd, arg;
        ss >> cmd;
        std::getline(ss >> std::ws, arg);

        // Convert command to uppercase
        for (auto &c : cmd) c = toupper(c);

        // ====================================================================
        // FTP COMMAND HANDLING
        // ====================================================================
        
        // 1. Authenticate USER
        if (cmd == "USER") {
            session.username = arg;
            send_reply(client_fd, 331, "User name okay, need password.");
        }

        // 2. Authenticate PASS
        else if (cmd == "PASS") {
            if (session.username == "admin" && arg == "123456") {
                session.is_authenticated = true;
                send_reply(client_fd, 230, "User logged in, proceed.");
            } else {
                send_reply(client_fd, 530, "Not logged in, incorrect password.");
            }
        }

        // Other commands require authentication
        else if (!session.is_authenticated) {
            send_reply(client_fd, 530, "Please login with USER and PASS.");
        }

        // 3. PWD command
        else if (cmd == "PWD") {
            send_reply(client_fd, 257, "\"" + session.current_dir + "\" is current directory.");
        }

        // 4. PASV command
        else if (cmd == "PASV") {
            int port = udp_prepare_passive_listener(&session);

            // Return passive mode information
            send_reply(client_fd, 227, "Entering Passive Mode (127,0,0,1,195,80)");
        }

        // 5. RETR command (Download file)
        else if (cmd == "RETR") {
            if (arg.empty()) {
                send_reply(client_fd, 501, "Syntax error in parameters.");
            } else {
                send_reply(client_fd, 150, "File status okay, opening UDP data connection.");
                
                // Call UDP module to send file
                TransferResult res = udp_send_file(&session, arg);
                
                if (res.is_success) {
                    send_reply(client_fd, 226, "Transfer complete.");
                } else {
                    send_reply(client_fd, 426, "Connection closed; transfer aborted.");
                }
            }
        }

        // 6. NOOP command
        else if (cmd == "NOOP") {
            send_reply(client_fd, 200, "NOOP ok.");
        }

        // 7. QUIT command
        else if (cmd == "QUIT") {
            send_reply(client_fd, 221, "Service closing control connection. Goodbye.");
            break;
        }

        // Unsupported command
        else {
            send_reply(client_fd, 502, "Command not implemented.");
        }
    }

    close(client_fd);
}

int main() {

    // Create TCP socket
    int server_fd = socket(AF_INET, SOCK_STREAM, 0);

    if (server_fd == 0) {
        perror("Socket failed");
        return -1;
    }

    // Allow port reuse after restart
    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in address;
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = INADDR_ANY;
    address.sin_port = htons(TCP_PORT);

    // Bind socket to server address
    if (bind(server_fd, (struct sockaddr*)&address, sizeof(address)) < 0) {
        perror("Bind failed");
        return -1;
    }

    // Start listening for clients
    if (listen(server_fd, 10) < 0) {
        perror("Listen failed");
        return -1;
    }

    std::cout << "====================================================\n";
    std::cout << "[TCP Control Server] Listening on Port " << TCP_PORT << "...\n";
    std::cout << "====================================================\n";

    while (true) {

        sockaddr_in client_addr;
        socklen_t addrlen = sizeof(client_addr);

        // Accept new client connection
        int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &addrlen);

        if (client_fd < 0) {
            perror("Accept error");
            continue;
        }

        std::cout << "[TCP Control]: New client connected! Socket FD: " 
                  << client_fd << std::endl;

        // Create independent thread for each client
        std::thread client_thread(handle_client, client_fd);

        // Detach thread so server can accept new clients
        client_thread.detach();
    }

    close(server_fd);
    return 0;
}