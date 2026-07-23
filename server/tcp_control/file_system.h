#ifndef FILE_SYSTEM_H
#define FILE_SYSTEM_H

#include <string>
#include <vector>

namespace FileSystem {

    // Check if file or directory exists
    bool exists(const std::string& path);

    // Check if path is a directory
    bool is_directory(const std::string& path);

    // Create a new directory (MKD)
    bool create_directory(const std::string& path);

    // Remove an empty directory (RMD)
    bool remove_directory(const std::string& path);

    // Get detailed directory listing for LIST command
    std::string get_directory_listing(const std::string& path);

    // Calculate SHA-256 hash of a file for HASH command
    std::string calculate_sha256(const std::string& filepath);
}

#endif // FILE_SYSTEM_H