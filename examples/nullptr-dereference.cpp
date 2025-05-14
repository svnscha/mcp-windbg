// nullptr-dereference.cpp
#include <iostream>

int main()
{
    int *ptr = nullptr;
    int value = *ptr;
    std::cout << value << std::endl;
    return 0;
}
