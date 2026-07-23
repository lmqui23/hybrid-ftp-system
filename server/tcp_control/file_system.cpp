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

    SHA256_Update(&sha256, buffer, file.gcount());


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

} // namespace FileSystem