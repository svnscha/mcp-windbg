// heap-buffer-overflow.cpp
#include <cstring>

int main()
{
    char *buf = new char[8];
    memset(buf, 'A', 16);
    delete[] buf;
    return 0;
}
