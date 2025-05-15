// complex-struct-nullptr.cpp
#include <iostream>
#include <cstring>
#include <ctime>
#include <string>

struct Address {
    char street[32];
    char city[16];
    int zip;
};

struct Metadata {
    time_t created;
    int version;
    char tag[8];
};

struct UserProfile {
    int id;
    char name[32];
    Address address;
    Metadata meta;
    double balance;
    int* preferences = nullptr;
    size_t pref_count;
    char* notes = nullptr;
};

void print_profile(const UserProfile* profile) {
    std::cout << "ID: " << profile->id << "\n";
    std::cout << "Name: " << profile->name << "\n";
    std::cout << "Address: " << profile->address.street << ", " << profile->address.city << ", " << profile->address.zip << "\n";
    std::cout << "Created: " << std::ctime(&profile->meta.created);
    std::cout << "Version: " << profile->meta.version << ", Tag: " << profile->meta.tag << "\n";
    std::cout << "Balance: $" << profile->balance << "\n";
    std::cout << "Preferences: ";
    for (size_t i = 0; i < profile->pref_count; ++i) {
        std::cout << profile->preferences[i] << " ";
    }
    std::cout << "\nNotes: " << profile->notes << "\n";
}

int main() {
    UserProfile* user = new UserProfile();
    user->id = 42;
    strcpy(user->name, "Ada Lovelace");
    strcpy(user->address.street, "123 Computing Ave");
    strcpy(user->address.city, "London");
    user->address.zip = 12345;
    user->meta.created = std::time(nullptr);
    user->meta.version = 1;
    strcpy(user->meta.tag, "alpha");
    user->balance = 1234.56;
    user->pref_count = 3;
    user->preferences = new int[user->pref_count]{1, 2, 3};
    print_profile(user);
    delete[] user->preferences;
    delete user;
    return 0;
}
