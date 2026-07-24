#define OPENSSL_SUPPRESS_DEPRECATED

#include "file_system.h"
#include <iostream>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <sys/stat.h>
#include <dirent.h>
#include <unistd.h>
#include <openssl/sha.h> // OpenSSL library for SHA-256 hashing
#include <chrono>
#include <random>

namespace FileSystem {

// Check if file or directory exists
bool exists(const std::string& path) {
    struct stat buffer;
    return (stat(path.c_str(), &buffer) == 0);
}

// Check if path is a directory
bool is_directory(const std::string& path) {
    struct stat buffer;
    if (stat(path.c_str(), &buffer) != 0) return false;
    return S_ISDIR(buffer.st_mode);
}

// Create a new directory
bool create_directory(const std::string& path) {
    // Create directory with permission 0755
    return (mkdir(path.c_str(), 0755) == 0);
}

// Remove an empty directory
bool remove_directory(const std::string& path) {
    return (rmdir(path.c_str()) == 0);
}

// Generate detailed directory listing for LIST command
std::string get_directory_listing(const std::string& path) {
    DIR* dir = opendir(path.c_str());
    if (!dir) return "";

    std::stringstream ss;
    struct dirent* entry;
    struct stat file_stat;

    while ((entry = readdir(dir)) != nullptr) {
        std::string name = entry->d_name;

        // Skip current and parent directories
        if (name == "." || name == "..") continue;

        std::string full_path = path + "/" + name;

        if (stat(full_path.c_str(), &file_stat) == 0) {
            // Format output similar to "ls -l"
            bool is_dir = S_ISDIR(file_stat.st_mode);

            ss << (is_dir ? "d" : "-") 
               << "rwxr-xr-x 1 user group " 
               << std::setw(10) << file_stat.st_size << " "
               << name << "\r\n";
        }
    }

    closedir(dir);
    return ss.str();
}

// Calculate SHA-256 hash of a file
std::string calculate_sha256(const std::string& filepath) {
    std::ifstream file(filepath, std::ios::binary);
    if (!file) return "";

    SHA256_CTX sha256;
    SHA256_Init(&sha256);

    char buffer[4096];
    while (file.read(buffer, sizeof(buffer))) {
        SHA256_Update(&sha256, buffer, file.gcount());
    }
    
    // Đã sửa: Chỉ băm phần còn dư nếu file không chia hết cho 4096 bytes
    if (file.gcount() > 0) {
        SHA256_Update(&sha256, buffer, file.gcount());
    }

    unsigned char hash[SHA256_DIGEST_LENGTH];
    SHA256_Final(hash, &sha256);

    std::stringstream ss;
    for (int i = 0; i < SHA256_DIGEST_LENGTH; i++) {
        ss << std::hex 
           << std::setw(2) 
           << std::setfill('0') 
           << (int)hash[i];
    }

    return ss.str();
}

bool remove_file(const std::string& path) {
    return (unlink(path.c_str()) == 0);
}

bool rename_path(const std::string& old_path, const std::string& new_path) {
    return (rename(old_path.c_str(), new_path.c_str()) == 0);
}

long long get_file_size(const std::string& path) {
    struct stat buffer;
    if (stat(path.c_str(), &buffer) == 0 && S_ISREG(buffer.st_mode)) {
        return buffer.st_size;
    }
    return -1;
}

std::string get_file_mtime(const std::string& path) {
    struct stat buffer;
    if (stat(path.c_str(), &buffer) == 0) {
        char time_buf[20];
        struct tm* tm_info = gmtime(&buffer.st_mtime);
        strftime(time_buf, sizeof(time_buf), "%Y%m%d%H%M%S", tm_info);
        return std::string(time_buf);
    }
    return "";
}

std::string get_simple_listing(const std::string& path) {
    DIR* dir = opendir(path.c_str());
    if (!dir) return "";
    
    std::stringstream ss;
    struct dirent* entry;
    while ((entry = readdir(dir)) != nullptr) {
        std::string name = entry->d_name;
        if (name != "." && name != "..") {
            ss << name << "\r\n";
        }
    }
    closedir(dir);
    return ss.str();
}

std::string generate_unique_filename(const std::string& directory, int client_fd) {
    while (true) {
        auto now = std::chrono::high_resolution_clock::now();
        auto nanos = std::chrono::duration_cast<std::chrono::nanoseconds>(
            now.time_since_epoch()
        ).count();

        std::stringstream ss;
        ss << "file_" << nanos << "_" << getpid() << "_" << client_fd << ".tmp";
        
        std::string filename = ss.str();
        std::string full_path = directory + "/" + filename;

        if (!exists(full_path)) {
            return filename;
        }
    }
}

bool verify_user_credentials(const std::string& username, const std::string& password) {
    std::ifstream file("./storage/users.txt");
    if (!file.is_open()) return false;

    std::string line;
    while (std::getline(file, line)) {
        size_t colon = line.find(':');
        if (colon != std::string::npos) {
            std::string u = line.substr(0, colon);
            std::string p = line.substr(colon + 1);
            
            // Delete \r if file was created on Windows
            if (!p.empty() && p.back() == '\r') p.pop_back();
            if (u == username && p == password) return true;
        }
    }
    return false;
}

} // namespace FileSystem