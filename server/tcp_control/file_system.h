#ifndef FILE_SYSTEM_H
#define FILE_SYSTEM_H

#pragma once

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

    // Delete a file (DELE)
    bool remove_file(const std::string& path);

    // Rename a file or directory (RNFR / RNTO)
    bool rename_path(const std::string& old_path, const std::string& new_path);

    // Get file size in bytes (SIZE)
    long long get_file_size(const std::string& path);

    // Get file last modification time (MDTM)
    std::string get_file_mtime(const std::string& path);

    // Get simple file name list (NLST)
    std::string get_simple_listing(const std::string& path);

    // Generate unique temporary filename (STOU)
    std::string generate_unique_filename(const std::string& directory, int client_fd);

    // Verify user authentication against storage/users.txt (USER / PASS)
    bool verify_user_credentials(const std::string& username, const std::string& password);

} // namespace FileSystem

#endif // FILE_SYSTEM_H